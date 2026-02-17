"""Plain async functions for external API calls.

No LLM involvement — these are direct HTTP calls to:
- Semantic Scholar API (author search, publications, citation metrics)
- Serper.dev (web search, Google Scholar search — replaces Tavily)
- Jina.ai Reader (page content extraction, with httpx+BS4 fallback)
- Google Scholar (scraping citation metrics from profile pages)
- Local document text extraction (pypdf, python-docx)
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"
SERPER_API_URL = "https://google.serper.dev"
JINA_READER_URL = "https://r.jina.ai/"


# ===================================================================
# Semantic Scholar
# ===================================================================


async def search_scholar(
    name: str, affiliation: str | None = None
) -> list[dict]:
    """Search for an author on Semantic Scholar."""
    async with httpx.AsyncClient() as client:
        query = f"{name} {affiliation}" if affiliation else name
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_API}/author/search",
            params={
                "query": query,
                "limit": 5,
                "fields": "name,affiliations,paperCount,citationCount,hIndex",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [
            {
                "author_id": r.get("authorId"),
                "name": r.get("name"),
                "affiliations": r.get("affiliations", []),
            }
            for r in data
        ]


async def get_publications(
    scholar_id: str, limit: int = 20, years: int = 5
) -> list[dict]:
    """Get an author's recent publications from Semantic Scholar."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_API}/author/{scholar_id}/papers",
            params={
                "fields": "title,authors,year,venue,abstract,citationCount,url",
                "limit": limit,
            },
            timeout=30,
        )
        resp.raise_for_status()
        papers = resp.json().get("data", [])

        if years:
            current_year = datetime.now().year
            papers = [
                p
                for p in papers
                if p.get("year") and p["year"] >= current_year - years
            ]

        return [
            {
                "title": p.get("title"),
                "authors": [a.get("name") for a in p.get("authors", [])],
                "year": p.get("year"),
                "venue": p.get("venue"),
                "abstract": p.get("abstract"),
                "citation_count": p.get("citationCount", 0),
                "url": p.get("url"),
            }
            for p in papers
        ]


async def get_citation_metrics(scholar_id: str) -> dict:
    """Get citation metrics for an author from Semantic Scholar."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_API}/author/{scholar_id}",
            params={
                "fields": "name,affiliations,paperCount,citationCount,hIndex",
            },
            timeout=30,
        )
        if resp.status_code == 404:
            return {"error": "Author not found"}
        resp.raise_for_status()
        details = resp.json()
        return {
            "h_index": details.get("hIndex", 0),
            "total_citations": details.get("citationCount", 0),
            "paper_count": details.get("paperCount", 0),
        }


# ===================================================================
# Google Scholar Scraping
# ===================================================================


async def scrape_google_scholar_metrics(google_scholar_url: str) -> dict:
    """Scrape citation metrics from a Google Scholar profile page."""
    if not google_scholar_url:
        return {"error": "No Google Scholar URL provided"}
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                google_scholar_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    )
                },
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            metrics: dict = {}
            rows = soup.select("table#gsc_rsb_st tr")
            for row in rows:
                cells = row.find_all("td", class_="gsc_rsb_std")
                if not cells:
                    continue
                label_elem = row.find("a", class_="gsc_rsb_f")
                if not label_elem:
                    continue
                label = label_elem.get_text(strip=True).lower()
                if len(cells) >= 1:
                    value = cells[0].get_text(strip=True)
                    try:
                        value = int(value)
                    except ValueError:
                        value = 0
                    if "citation" in label:
                        metrics["total_citations"] = value
                    elif "h-index" in label:
                        metrics["h_index"] = value
            return metrics
    except Exception as e:
        return {"error": f"Failed to scrape Google Scholar: {str(e)}"}


# ===================================================================
# Serper.dev Web Search (replaces Tavily)
# ===================================================================


async def search_web(query: str, num_results: int = 5) -> list[str]:
    """Search the web using Serper.dev. Returns list of URLs."""
    if not settings.serper_api_key:
        logger.warning("SERPER_API_KEY not configured")
        return []
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
    except Exception as e:
        logger.error(f"Serper search failed: {e}")
        return []


async def search_google_scholar(
    query: str, num_results: int = 5
) -> list[dict]:
    """Search Google Scholar using Serper.dev. Returns list of result dicts."""
    if not settings.serper_api_key:
        logger.warning("SERPER_API_KEY not configured")
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERPER_API_URL}/scholar",
                json={"q": query, "num": num_results},
                headers={"X-API-KEY": settings.serper_api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("organic", [])
    except Exception as e:
        logger.error(f"Serper Scholar search failed: {e}")
        return []


async def find_google_scholar_url(
    professor_name: str, domain: str
) -> str | None:
    """Search for a professor's Google Scholar profile URL via Serper."""
    query = f'"{professor_name}" {domain}'
    results = await search_google_scholar(query, num_results=3)
    for result in results:
        link = result.get("link", "")
        if "scholar.google.com" in link and "user=" in link:
            return link
    return None


# ===================================================================
# Jina.ai Reader (page content extraction)
# ===================================================================


async def fetch_page_content(url: str) -> str:
    """Fetch and extract page content.

    Uses Jina.ai Reader for clean markdown extraction.
    Falls back to raw httpx+BS4 if Jina fails or is not configured.
    """
    if settings.jina_api_key:
        try:
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {settings.jina_api_key}",
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{JINA_READER_URL}{url}",
                    headers=headers,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data.get("data", {}).get("content", "")
                if content:
                    return content[:25000]
        except Exception as e:
            logger.warning(
                f"Jina Reader failed for {url}, falling back to httpx: {e}"
            )

    return await _fetch_page_raw(url)


async def _fetch_page_raw(url: str) -> str:
    """Fallback: fetch page with httpx and do basic text extraction."""
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
        html = resp.text

    # Strip scripts and styles, then remove tags
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE
    )

    # Preserve links in markdown format for directory discovery
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


def extract_text_from_file(file_path: str) -> str:
    """Extract raw text from PDF, DOCX, or TXT file."""
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        return "".join(page.extract_text() or "" for page in reader.pages)
    elif ext == ".docx":
        from docx import Document

        doc = Document(file_path)
        return "\n".join(para.text for para in doc.paragraphs)
    elif ext == ".txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported file type: {ext}")
