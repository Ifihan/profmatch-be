"""Plain async functions for external API calls.

No LLM involvement -- these are direct HTTP calls to:
- Serper.dev (web search, used by fallback scraping path)
- trafilatura (page content extraction, with httpx+BS4 fallback)
- Local document text extraction (pymupdf, python-docx)
"""

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SERPER_API_URL = "https://google.serper.dev"

# Shared HTTP clients (reused across requests to avoid TLS handshake overhead)
_serper_client: httpx.AsyncClient | None = None
_http_client: httpx.AsyncClient | None = None

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get_serper_client() -> httpx.AsyncClient:
    """Get or create shared Serper API client."""
    global _serper_client
    if _serper_client is None or _serper_client.is_closed:
        _serper_client = httpx.AsyncClient(
            base_url=SERPER_API_URL,
            headers={"X-API-KEY": settings.serper_api_key or ""},
            timeout=15,
        )
    return _serper_client


def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client for page fetching."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
            timeout=30,
        )
    return _http_client


async def close_clients() -> None:
    """Close shared HTTP clients (call on app shutdown)."""
    global _serper_client, _http_client
    if _serper_client and not _serper_client.is_closed:
        await _serper_client.aclose()
        _serper_client = None
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


# ===================================================================
# Serper.dev Web Search (used by fallback scraping path)
# ===================================================================


async def search_web(*, query: str, num_results: int = 5) -> list[str]:
    """Search the web using Serper.dev. Returns list of URLs."""
    if not settings.serper_api_key:
        raise ValueError("SERPER_API_KEY not configured")
    try:
        client = _get_serper_client()
        resp = await client.post(
            "/search",
            json={"q": query, "num": num_results},
        )
        resp.raise_for_status()
        data = resp.json()
        urls = []
        for result in data.get("organic", []):
            url = result.get("link", "")
            if url and url not in urls:
                urls.append(url)
        return urls
    except ValueError:
        raise
    except Exception:
        return []


# ===================================================================
# Page Content Extraction (trafilatura with httpx+BS4 fallback)
# ===================================================================


async def fetch_page_content(*, url: str) -> str:
    """Fetch and extract page content using trafilatura.

    Falls back to raw httpx+BS4 if trafilatura returns nothing.
    """
    import trafilatura

    try:
        html = await _fetch_html(url=url)
        # trafilatura is CPU-bound, run in thread pool
        content = await asyncio.to_thread(
            trafilatura.extract,
            html,
            include_links=True,
            include_tables=True,
            output_format="txt",
            url=url,
        )
        if content:
            return content[:25000]
    except Exception:
        pass

    return await _fetch_page_raw(url=url)


async def _fetch_html(*, url: str) -> str:
    """Fetch raw HTML from a URL."""
    client = _get_http_client()
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.text


async def _fetch_page_raw(*, url: str) -> str:
    """Fallback: fetch page with httpx and do basic text extraction."""
    html = await _fetch_html(url=url)

    # strip scripts and styles, then remove tags
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE
    )

    # preserve links in markdown format for directory discovery
    def _replace_link(match: re.Match) -> str:
        href = match.group(1)
        content = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        if not href.startswith(("http", "https", "mailto:", "tel:")):
            if href.startswith("/"):
                parsed = urlparse(url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            else:
                href = f"{url.rstrip('/')}/{href}"
        return f" [{content}]({href}) "

    text = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
        _replace_link,
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:25000]


# ===================================================================
# Document Text Extraction (no LLM)
# ===================================================================


def extract_text_from_file(*, file_path: str) -> str:
    """Extract raw text from PDF, DOCX, or TXT file."""
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        import pymupdf

        doc = pymupdf.open(file_path)
        return "".join(page.get_text() for page in doc)
    elif ext == ".docx":
        from docx import Document

        doc = Document(file_path)
        return "\n".join(para.text for para in doc.paragraphs)
    elif ext == ".txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported file type: {ext}")


# ===================================================================
# Professor Supplementary Lookups (Google Scholar, Email, Homepage)
# ===================================================================

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


async def _serper_search_with_snippets(
    *, query: str, num_results: int = 3
) -> list[dict]:
    """Search using Serper.dev and return full result dicts (link, title, snippet)."""
    if not settings.serper_api_key:
        return []
    try:
        client = _get_serper_client()
        resp = await client.post(
            "/search",
            json={"q": query, "num": num_results},
        )
        resp.raise_for_status()
        return resp.json().get("organic", [])
    except Exception:
        return []


async def search_google_scholar_url(*, name: str, university: str) -> str | None:
    """Search for a professor's Google Scholar profile URL.

    Returns the Google Scholar citations URL or None if not found.
    """
    query = f'"{name}" site:scholar.google.com'
    results = await _serper_search_with_snippets(query=query, num_results=3)

    for result in results:
        url = result.get("link", "")
        if "scholar.google.com/citations" in url:
            logger.debug("found Google Scholar URL for %s: %s", name, url)
            return url

    return None


async def search_professor_contact(
    *, name: str, university_domain: str
) -> dict[str, str | None]:
    """Search for a professor's email and homepage on their university site.

    Returns dict with 'email' and 'homepage' keys (values may be None).
    """
    domain = urlparse(
        university_domain if "://" in university_domain else f"https://{university_domain}"
    ).netloc.replace("www.", "")

    query = f'"{name}" site:{domain}'
    results = await _serper_search_with_snippets(query=query, num_results=5)

    email = None
    homepage = None

    # Patterns that indicate directory/listing pages (not personal profiles)
    listing_patterns = ["?page=", "/search/", "/search?", "lastname=", "firstname="]

    for result in results:
        url = result.get("link", "")
        snippet = result.get("snippet", "")

        # Extract homepage — prefer URLs with professor name in path,
        # skip generic directory/listing pages
        if not homepage and domain in url:
            url_lower = url.lower()
            if not any(p in url_lower for p in listing_patterns):
                homepage = url

        # Extract email from snippets
        if not email:
            emails_found = _EMAIL_PATTERN.findall(snippet)
            for e in emails_found:
                if domain in e:
                    email = e
                    break

    # If no email in snippets, try fetching the homepage content
    if not email and homepage:
        try:
            html = await _fetch_html(url=homepage)
            emails_found = _EMAIL_PATTERN.findall(html)
            for e in emails_found:
                if domain in e:
                    email = e
                    break
        except Exception:
            pass

    return {"email": email, "homepage": homepage}
