"""OpenAlex client — primary publication/metrics source; `mailto` enters the polite pool."""
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from app.core.config import settings

BASE = "https://api.openalex.org"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def find_author(
    client: httpx.AsyncClient, name: str, institution_id: str | None = None
) -> dict | None:
    params = {"search": name, "per_page": 1, "mailto": settings.openalex_mailto}
    if institution_id:
        # Narrow to the university to avoid grabbing a same-named author elsewhere.
        params["filter"] = f"last_known_institutions.id:{institution_id.rstrip('/').rsplit('/', 1)[-1]}"
    r = await client.get(f"{BASE}/authors", params=params, timeout=20)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def find_author_by_orcid(client: httpx.AsyncClient, orcid: str) -> dict | None:
    """Precise lookup by ORCID — the canonical disambiguator for common names."""
    params = {"filter": f"orcid:{orcid}", "per_page": 1, "mailto": settings.openalex_mailto}
    r = await client.get(f"{BASE}/authors", params=params, timeout=20)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def find_institution(client: httpx.AsyncClient, name: str) -> dict | None:
    params = {"search": name, "per_page": 1, "mailto": settings.openalex_mailto}
    r = await client.get(f"{BASE}/institutions", params=params, timeout=20)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def _homepage_host(inst: dict) -> str:
    host = urlparse(inst.get("homepage_url") or "").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def find_institution_by_domain(
    client: httpx.AsyncClient, domain: str, name_hint: str, queries: list[str]
) -> dict | None:
    """Resolve an institution and verify it by homepage domain to avoid wrong-name matches."""
    for q in dict.fromkeys([name_hint, *queries]):
        if not q:
            continue
        r = await client.get(
            f"{BASE}/institutions",
            params={"search": q, "per_page": 5, "mailto": settings.openalex_mailto},
            timeout=20,
        )
        r.raise_for_status()
        for inst in r.json().get("results", []):
            host = _homepage_host(inst)
            if host and (host == domain or host.endswith("." + domain) or domain.endswith("." + host)):
                return inst
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def institution_authors(
    client: httpx.AsyncClient, institution_id: str, limit: int = 25
) -> list[dict]:
    """Top authors at an institution — discovery fallback when the site scrape finds too few."""
    params = {
        "filter": f"last_known_institutions.id:{institution_id}",
        "sort": "cited_by_count:desc",
        "per_page": limit,
        "select": "id,display_name,orcid,topics",
        "mailto": settings.openalex_mailto,
    }
    r = await client.get(f"{BASE}/authors", params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("results", [])


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def works_by_field(
    client: httpx.AsyncClient, institution_id: str, query: str, limit: int = 100
) -> list[dict]:
    """Authors at an institution who publish on `query`, ranked by how often they
    appear among the top-cited matching works — field-relevant candidate discovery."""
    iid = institution_id.rstrip("/").rsplit("/", 1)[-1]
    params = {
        "filter": f"authorships.institutions.id:{iid}",
        "search": query,
        "sort": "cited_by_count:desc",
        "per_page": limit,
        "select": "authorships",
        "mailto": settings.openalex_mailto,
    }
    r = await client.get(f"{BASE}/works", params=params, timeout=20)
    r.raise_for_status()
    agg: dict[str, dict] = {}
    for work in r.json().get("results", []):
        for a in work.get("authorships", []):
            if iid not in {i.get("id", "").rstrip("/").rsplit("/", 1)[-1] for i in a.get("institutions", [])}:
                continue
            author = a.get("author") or {}
            aid = author.get("id")
            if not aid or not author.get("display_name"):
                continue
            entry = agg.setdefault(aid, {"id": aid, "display_name": author["display_name"], "score": 0})
            entry["score"] += 1
    return sorted(agg.values(), key=lambda x: x["score"], reverse=True)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def get_author(client: httpx.AsyncClient, author_id: str) -> dict | None:
    """Fetch a full author record (with summary_stats) by OpenAlex id or URL."""
    aid = author_id.rstrip("/").rsplit("/", 1)[-1]
    r = await client.get(
        f"{BASE}/authors/{aid}", params={"mailto": settings.openalex_mailto}, timeout=20
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


async def author_works(client: httpx.AsyncClient, author_id: str, limit: int = 10) -> list[dict]:
    params = {
        "filter": f"author.id:{author_id}",
        "sort": "cited_by_count:desc",
        "per_page": limit,
        "select": "title,publication_year,cited_by_count,doi,primary_location,"
                  "authorships,abstract_inverted_index",
        "mailto": settings.openalex_mailto,
    }
    r = await client.get(f"{BASE}/works", params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("results", [])


def parse_metrics(author: dict) -> dict:
    summary = author.get("summary_stats", {}) or {}
    return {
        "h_index": summary.get("h_index"),
        "citations": author.get("cited_by_count"),
        "i10_index": summary.get("i10_index"),
    }


def _reconstruct_abstract(inverted: dict | None) -> str | None:
    """OpenAlex stores abstracts as an inverted index {word: [positions]}."""
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(word for _, word in positions) or None


def parse_work(w: dict) -> dict:
    """Map an OpenAlex work to the publication contract."""
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    authors = [
        (a.get("author") or {}).get("display_name")
        for a in w.get("authorships", [])
        if (a.get("author") or {}).get("display_name")
    ]
    return {
        "title": w.get("title"),
        "authors": authors,
        "year": w.get("publication_year"),
        "venue": venue,
        "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
        "citation_count": w.get("cited_by_count"),
        "url": (w.get("primary_location") or {}).get("landing_page_url") or w.get("doi"),
    }
