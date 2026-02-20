"""Plain async functions for external API calls.

No LLM involvement -- these are direct HTTP calls to:
- Serper.dev (web search, used by fallback scraping path)
- trafilatura (page content extraction, with httpx+BS4 fallback)
- Local document text extraction (pymupdf, python-docx)
"""

import asyncio
import re
from pathlib import Path

import httpx

from app.config import settings

SERPER_API_URL = "https://google.serper.dev"


# ===================================================================
# Serper.dev Web Search (used by fallback scraping path)
# ===================================================================


async def search_web(*, query: str, num_results: int = 5) -> list[str]:
    """Search the web using Serper.dev. Returns list of URLs."""
    if not settings.serper_api_key:
        raise ValueError("SERPER_API_KEY not configured")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERPER_API_URL}/search",
                json={"q": query, "num": num_results},
                headers={"X-API-KEY": settings.serper_api_key},
                timeout=15,
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, timeout=30, headers=headers)
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
        from urllib.parse import urlparse

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
