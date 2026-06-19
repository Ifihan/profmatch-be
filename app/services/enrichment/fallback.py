"""Secondary/tertiary enrichment when OpenAlex has no match; OpenAlex stays canonical for citations."""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from app.core.config import settings

S2_BASE = "https://api.semanticscholar.org/graph/v1"
CROSSREF_BASE = "https://api.crossref.org"
ORCID_BASE = "https://pub.orcid.org/v3.0"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def semantic_scholar_author(client: httpx.AsyncClient, name: str) -> dict | None:
    headers = {}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    params = {"query": name, "fields": "name,hIndex,citationCount,papers.title,papers.year", "limit": 1}
    r = await client.get(f"{S2_BASE}/author/search", params=params, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def orcid_id(client: httpx.AsyncClient, name: str, institution: str | None) -> str | None:
    """Resolve a single ORCID for a name (+ affiliation); returns only on an unambiguous match."""
    query = f'family-name:"{name.split()[-1]}" AND given-names:"{name.split()[0]}"' if name.split() else name
    if institution:
        query += f' AND affiliation-org-name:"{institution}"'
    r = await client.get(
        f"{ORCID_BASE}/expanded-search",
        params={"q": query, "rows": 2},
        headers={"Accept": "application/json"},
        timeout=20,
    )
    r.raise_for_status()
    results = r.json().get("expanded-result") or []
    return results[0]["orcid-id"] if len(results) == 1 else None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def crossref_works(client: httpx.AsyncClient, name: str, limit: int = 8) -> list[dict]:
    """Tertiary source: Crossref fills publications only (no author metrics)."""
    params = {
        "query.author": name,
        "rows": limit,
        "sort": "is-referenced-by-count",
        "order": "desc",
        "select": "title,author,issued,container-title,DOI,is-referenced-by-count,URL",
    }
    if settings.crossref_mailto:
        params["mailto"] = settings.crossref_mailto
    r = await client.get(f"{CROSSREF_BASE}/works", params=params, timeout=20)
    r.raise_for_status()
    items = r.json().get("message", {}).get("items", [])
    return [_crossref_work(it) for it in items]


def _crossref_work(item: dict) -> dict:
    titles = item.get("title") or []
    venues = item.get("container-title") or []
    authors = [
        " ".join(p for p in (a.get("given"), a.get("family")) if p)
        for a in item.get("author", [])
    ]
    year = None
    parts = (item.get("issued") or {}).get("date-parts") or [[]]
    if parts and parts[0]:
        year = parts[0][0]
    return {
        "title": titles[0] if titles else None,
        "authors": authors,
        "year": year,
        "venue": venues[0] if venues else None,
        "abstract": None,
        "citation_count": item.get("is-referenced-by-count"),
        "url": item.get("URL"),
    }
