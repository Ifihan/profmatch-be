"""OpenAlex API client for academic author discovery and enrichment.

Free, open academic database with 93M+ authors. No API key required.
Uses the polite pool (mailto header) for faster rate limits.

API docs: https://docs.openalex.org/
"""

from datetime import datetime

import httpx

OPENALEX_API = "https://api.openalex.org"
MAILTO = "profmatch@example.com"

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create a shared async client with polite pool header."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=OPENALEX_API,
            headers={"User-Agent": f"mailto:{MAILTO}"},
            timeout=30,
        )
    return _client


async def close_client() -> None:
    """Close the shared client (call on app shutdown)."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def resolve_institution(*, query: str) -> dict | None:
    """Search OpenAlex for an institution by name or domain.

    Returns dict with id, display_name, ror, country_code, type, works_count,
    cited_by_count, or None if not found.
    """
    client = _get_client()

    # try domain-based filter first (more precise)
    domain = _extract_domain_from_query(query)
    if domain:
        resp = await client.get(
            "/institutions",
            params={
                "filter": f"domains.domain:{domain}",
                "per_page": 1,
            },
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return _parse_institution(results[0])

    # fall back to text search
    resp = await client.get(
        "/institutions",
        params={
            "search": query,
            "per_page": 3,
        },
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return None

    return _parse_institution(results[0])


async def get_authors_by_institution(
    *,
    institution_id: str,
    topics: list[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch authors affiliated with an institution, optionally filtered by topics.

    Returns list of dicts with: openalex_id, name, h_index, i10_index,
    cited_by_count, works_count, topics, orcid.
    """
    client = _get_client()

    filter_parts = [f"last_known_institutions.id:{institution_id}"]

    if topics:
        # search for authors whose topics match any of the provided keywords
        # use concepts.display_name search via topics filter
        topic_query = "|".join(topics[:5])
        filter_parts.append(f"topics.display_name.search:{topic_query}")

    resp = await client.get(
        "/authors",
        params={
            "filter": ",".join(filter_parts),
            "sort": "cited_by_count:desc",
            "per_page": min(limit, 200),
            "select": (
                "id,display_name,ids,summary_stats,works_count,"
                "cited_by_count,topics,last_known_institutions,affiliations"
            ),
        },
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])

    return [_parse_author(a) for a in results]


async def get_author_works(
    *,
    author_id: str,
    limit: int = 20,
    years: int = 5,
) -> list[dict]:
    """Fetch recent works for an author.

    Returns list of dicts with: openalex_id, title, abstract, citation_count,
    publication_year, topics, venue, doi, authors.
    """
    client = _get_client()

    cutoff_year = datetime.now().year - years

    resp = await client.get(
        "/works",
        params={
            "filter": (
                f"authorships.author.id:{author_id},"
                f"publication_year:>{cutoff_year}"
            ),
            "sort": "cited_by_count:desc",
            "per_page": min(limit, 200),
            "select": (
                "id,title,authorships,publication_year,primary_location,"
                "cited_by_count,topics,doi,abstract_inverted_index"
            ),
        },
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])

    return [_parse_work(w) for w in results]


# --- internal helpers ---


def _extract_domain_from_query(query: str) -> str | None:
    """Try to extract a domain from a query string (e.g. 'mit.edu', 'https://www.mit.edu/cs')."""
    query = query.strip().lower()

    # strip protocol
    for prefix in ("https://", "http://"):
        if query.startswith(prefix):
            query = query[len(prefix):]
            break

    # strip www.
    if query.startswith("www."):
        query = query[4:]

    # strip path
    domain = query.split("/")[0]

    # check if it looks like a domain
    if "." in domain and " " not in domain:
        return domain

    return None


def _parse_institution(raw: dict) -> dict:
    """Parse an OpenAlex institution response into a clean dict."""
    return {
        "id": raw.get("id", ""),
        "display_name": raw.get("display_name", ""),
        "ror": raw.get("ror"),
        "country_code": raw.get("country_code"),
        "type": raw.get("type"),
        "works_count": raw.get("works_count", 0),
        "cited_by_count": raw.get("cited_by_count", 0),
    }


def _parse_author(raw: dict) -> dict:
    """Parse an OpenAlex author response into a clean dict."""
    summary = raw.get("summary_stats") or {}
    ids = raw.get("ids") or {}

    # extract topic names from the topics list (top-level topics)
    topics_raw = raw.get("topics") or []
    topic_names = []
    topic_details = []
    for t in topics_raw[:20]:
        name = t.get("display_name", "")
        if name:
            topic_names.append(name)
            topic_details.append({
                "name": name,
                "subfield": (t.get("subfield") or {}).get("display_name"),
                "field": (t.get("field") or {}).get("display_name"),
                "domain": (t.get("domain") or {}).get("display_name"),
            })

    return {
        "openalex_id": raw.get("id", ""),
        "name": raw.get("display_name", ""),
        "h_index": summary.get("h_index", 0),
        "i10_index": summary.get("i10_index", 0),
        "cited_by_count": raw.get("cited_by_count", 0),
        "works_count": raw.get("works_count", 0),
        "topics": topic_names,
        "topic_details": topic_details,
        "orcid": ids.get("orcid"),
    }


def _parse_work(raw: dict) -> dict:
    """Parse an OpenAlex work response into a clean dict."""
    # reconstruct abstract from inverted index
    abstract = _reconstruct_abstract(raw.get("abstract_inverted_index"))

    # extract venue from primary_location
    location = raw.get("primary_location") or {}
    source = location.get("source") or {}
    venue = source.get("display_name")

    # extract author names
    authorships = raw.get("authorships") or []
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in authorships
        if a.get("author", {}).get("display_name")
    ]

    # extract topic names
    topics_raw = raw.get("topics") or []
    topics = [t.get("display_name", "") for t in topics_raw[:10] if t.get("display_name")]

    return {
        "openalex_id": raw.get("id", ""),
        "title": raw.get("title", ""),
        "abstract": abstract,
        "citation_count": raw.get("cited_by_count", 0),
        "publication_year": raw.get("publication_year"),
        "topics": topics,
        "venue": venue,
        "doi": raw.get("doi"),
        "authors": authors,
    }


def _reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """Reconstruct abstract text from OpenAlex's inverted index format."""
    if not inverted_index:
        return None

    # inverted_index maps word -> list of positions
    # e.g. {"the": [0, 5], "cat": [1]} -> position-sorted words
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))

    word_positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in word_positions)
