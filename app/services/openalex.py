"""OpenAlex API client for academic author discovery and enrichment.

Free, open academic database with 93M+ authors. No API key required.
Uses the polite pool (mailto header) for faster rate limits.

API docs: https://docs.openalex.org/
"""

import logging
from collections import defaultdict
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

OPENALEX_API = "https://api.openalex.org"
MAILTO = "profmatch@example.com"

_client: httpx.AsyncClient | None = None

# In-memory cache for institution resolution (domain -> institution dict)
_institution_cache: dict[str, dict | None] = {}


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

    Uses text search with domain-based homepage verification.
    Caches successful results in memory to avoid repeated API calls.

    Returns dict with id, display_name, ror, country_code, type, works_count,
    cited_by_count, homepage_url, or None if not found.
    """
    cache_key = query.strip().lower()
    if cache_key in _institution_cache:
        logger.debug("institution cache hit for %s", cache_key)
        return _institution_cache[cache_key]

    client = _get_client()
    domain = _extract_domain_from_query(query)

    if domain:
        # Strategy A: search with domain prefix (e.g., "wits" from "wits.ac.za")
        # Then verify by checking if the result's homepage_url contains the domain
        search_term = domain.split(".")[0]
        inst = await _search_institution_with_domain_check(
            client=client, search_term=search_term, domain=domain
        )
        if inst:
            _institution_cache[cache_key] = inst
            return inst

        # Strategy B: domain prefix was ambiguous (e.g., "cam" for cam.ac.uk)
        # Try longer domain portions
        domain_without_tld = domain.rsplit(".", 1)[0]  # "cam.ac" from "cam.ac.uk"
        if domain_without_tld != search_term:
            inst = await _search_institution_with_domain_check(
                client=client, search_term=domain_without_tld, domain=domain
            )
            if inst:
                _institution_cache[cache_key] = inst
                return inst

        # Strategy C: fetch page title from the URL and search with that
        try:
            page_url = f"https://www.{domain}"
            title = await _fetch_page_title(url=page_url)
            if title:
                inst = await _search_institution_with_domain_check(
                    client=client, search_term=title, domain=domain
                )
                if inst:
                    _institution_cache[cache_key] = inst
                    return inst
        except Exception:
            pass
    else:
        # Query is an institution name, not a domain — direct text search
        try:
            resp = await client.get(
                "/institutions",
                params={"search": query, "per_page": 5},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                inst = _parse_institution(results[0])
                _institution_cache[cache_key] = inst
                logger.info("resolved institution: %s (id=%s)", inst["display_name"], inst["id"])
                return inst
        except Exception as e:
            logger.warning("institution text search failed for %s: %s", query, e)

    logger.warning("could not resolve institution for query: %s", query)
    # Don't cache None — allow retries with different strategies
    return None


async def _search_institution_with_domain_check(
    *, client: httpx.AsyncClient, search_term: str, domain: str
) -> dict | None:
    """Search OpenAlex institutions and verify result matches the expected domain."""
    try:
        logger.info("searching institutions: %s (verifying domain: %s)", search_term, domain)
        resp = await client.get(
            "/institutions",
            params={"search": search_term, "per_page": 5},
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])

        for r in results:
            homepage = (r.get("homepage_url") or "").lower()
            if domain in homepage:
                inst = _parse_institution(r)
                logger.info("resolved institution: %s (id=%s)", inst["display_name"], inst["id"])
                return inst

        return None
    except Exception as e:
        logger.warning("institution search failed for %s: %s", search_term, e)
        return None


async def _fetch_page_title(*, url: str) -> str | None:
    """Fetch a URL and extract the page title for institution name resolution."""
    import re

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            if match:
                title = match.group(1).strip()
                # Clean common suffixes like " - Home", " | Official Site"
                title = re.split(r"\s*[|\-–—]\s*", title)[0].strip()
                if len(title) > 3:
                    logger.info("extracted page title: %s", title)
                    return title
    except Exception:
        pass
    return None


async def get_authors_by_institution(
    *,
    institution_id: str,
    topics: list[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch authors affiliated with an institution, optionally filtered by topics.

    Returns list of dicts with: openalex_id, name, h_index, i10_index,
    cited_by_count, works_count, topics, orcid, last_known_institutions.
    """
    client = _get_client()

    filter_parts = [f"last_known_institutions.id:{institution_id}"]

    if topics:
        # search for authors whose topics match any of the provided keywords
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


async def search_author_by_name(
    *,
    name: str,
    institution_id: str | None = None,
) -> dict | None:
    """Search OpenAlex for an author by display name, optionally filtered by institution.

    Returns parsed author dict or None if not found.
    """
    client = _get_client()

    filter_parts = [f"display_name.search:{name}"]
    if institution_id:
        filter_parts.append(f"last_known_institutions.id:{institution_id}")

    resp = await client.get(
        "/authors",
        params={
            "filter": ",".join(filter_parts),
            "sort": "cited_by_count:desc",
            "per_page": 3,
            "select": (
                "id,display_name,ids,summary_stats,works_count,"
                "cited_by_count,topics,last_known_institutions,affiliations"
            ),
        },
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])

    if not results:
        return None

    # Return the best match (first result, sorted by citations)
    return _parse_author(results[0])


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


async def get_works_for_authors(
    *,
    author_ids: list[str],
    limit_per_author: int = 10,
    years: int = 5,
) -> dict[str, list[dict]]:
    """Fetch recent works for multiple authors in batched API calls.

    Returns dict mapping author OpenAlex ID -> list of work dicts.
    Batches author IDs into groups of 25 and fetches all batches in parallel.
    """
    if not author_ids:
        return {}

    import asyncio

    client = _get_client()
    cutoff_year = datetime.now().year - years
    batch_size = 25
    result: dict[str, list[dict]] = defaultdict(list)

    async def _fetch_batch(batch: list[str]) -> tuple[list[str], list[dict]]:
        author_filter = "|".join(batch)
        resp = await client.get(
            "/works",
            params={
                "filter": (
                    f"authorships.author.id:{author_filter},"
                    f"publication_year:>{cutoff_year}"
                ),
                "sort": "cited_by_count:desc",
                "per_page": 200,
                "select": (
                    "id,title,authorships,publication_year,primary_location,"
                    "cited_by_count,topics,doi,abstract_inverted_index"
                ),
            },
        )
        resp.raise_for_status()
        return batch, resp.json().get("results", [])

    batches = [author_ids[i : i + batch_size] for i in range(0, len(author_ids), batch_size)]
    batch_results = await asyncio.gather(
        *[_fetch_batch(b) for b in batches],
        return_exceptions=True,
    )

    for br in batch_results:
        if isinstance(br, Exception):
            logger.warning("batch works fetch failed: %s", br)
            continue
        batch, works = br
        batch_set = set(batch)
        for w in works:
            parsed = _parse_work(w)
            for authorship in w.get("authorships") or []:
                aid = (authorship.get("author") or {}).get("id", "")
                if aid in batch_set:
                    result[aid].append(parsed)
                    break

    # Trim to limit_per_author per author (already sorted by citations)
    return {
        aid: works[:limit_per_author]
        for aid, works in result.items()
    }


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
        "homepage_url": raw.get("homepage_url"),
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

    # extract last known institutions for affiliation verification
    last_known_raw = raw.get("last_known_institutions") or []
    last_known_institutions = []
    for inst in last_known_raw:
        last_known_institutions.append({
            "id": inst.get("id", ""),
            "display_name": inst.get("display_name", ""),
            "country_code": inst.get("country_code"),
            "type": inst.get("type"),
        })

    # extract department from affiliations if available
    affiliations_raw = raw.get("affiliations") or []
    department = None
    for aff in affiliations_raw:
        institution = aff.get("institution") or {}
        if institution.get("id") == (last_known_raw[0].get("id") if last_known_raw else None):
            # Get the most recent year's affiliation
            years = aff.get("years") or []
            if years:
                department = institution.get("display_name")
            break

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
        "last_known_institutions": last_known_institutions,
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
