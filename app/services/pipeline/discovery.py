"""Stage 2: LLM-guided faculty discovery — crawl the school's site (robots.txt + time budget), fall back to domain-verified OpenAlex when too few are found."""
import re
import time
from datetime import date
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from selectolax.parser import HTMLParser
from app.core.config import settings
from app.services import cache
from app.services.enrichment import openalex
from app.services.gemini import generate_json

_UA = "ProfMatchBot/1.0 (+https://profmatch.example)"
_UA_TOKEN = "ProfMatchBot"
_DEADLINE_SECONDS = 60
_MAX_PAGES = 12
_TARGET_FACULTY = 20
_MIN_FACULTY = 5
_RECENCY_YEARS = 3       # require recent institutional activity within this window
_RECENCY_MIN_WORKS = 2   # ...with at least this many works (single hits are noisy)

_GENERIC = {"ac", "edu", "co", "gov", "org", "com", "net", "sch", "uni", "univ", "www"}
_CCTLDS = {"za", "uk", "au", "ng", "ke", "gh", "in", "pk", "my", "sg", "nz", "ca", "us",
           "ie", "rs", "hr", "de", "fr", "es", "it", "nl", "se", "no", "fi", "dk", "ch",
           "at", "be", "pt", "gr", "pl", "cz", "br", "mx", "ar", "cl", "jp", "cn", "kr",
           "tr", "eg", "sa", "ae"}
_STAFF_HINTS = ("staff", "people", "academic", "faculty", "professor", "lecturer",
                "researcher", "member", "team", "directory")
_SECTION_HINTS = ("school", "department", "dept", "institute", "faculty of", "centre", "center")

# Ambiguous interest terms → concrete sub-queries, so OpenAlex hits the right concept
# cluster (e.g. computational, not biomimetic-materials, for "bio-inspired computing").
_TERM_EXPANSIONS = {
    "bio-inspired computing": ["neuromorphic computing", "spiking neural networks", "evolutionary computation"],
    "bio inspired computing": ["neuromorphic computing", "spiking neural networks", "evolutionary computation"],
    "bio-inspired": ["neuromorphic computing", "evolutionary computation", "swarm intelligence"],
}

_PAGE_PROMPT = """You are navigating a university website to find its ACADEMIC STAFF
for a student interested in: {field}.

From this page do two things:
1. Extract current academic staff listed here. Exclude visiting, adjunct, emeritus,
   honorary, retired, and purely administrative staff.
2. Pick up to 5 links most likely to lead to (more) relevant staff/people listings,
   preferring the department or school for that field.

Return ONLY JSON:
{{"faculty": [{{"name": str, "designation": str|null, "faculty": str|null,
   "email": str|null, "profile_url": str|null, "listed_interests": [str]}}],
  "next_urls": [str]}}

PAGE TEXT:
{text}

LINKS (url | anchor):
{links}
"""


async def run(
    university_url: str, interests: str, key_topics: list[str] | None = None,
    max_pages: int = _MAX_PAGES,
) -> list[dict]:
    url = str(university_url)
    domain = _registrable_domain(url)
    field_terms = _field_terms(interests)
    deadline = time.monotonic() + _DEADLINE_SECONDS
    robots: dict[str, RobotFileParser] = {}

    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": _UA}) as client:
        institution = await _resolve_institution(client, url, domain)
        institution_id = institution["id"] if institution else None
        university = institution["display_name"] if institution else _university_name(url)

        # Primary: field-relevant authors via OpenAlex works search (right people,
        # carrying author ids → precise enrichment).
        pool: list[dict] = []
        if institution_id:
            pool = await _openalex_by_field(client, institution_id, university, interests, key_topics)

        # Fallback when OpenAlex is thin (under-indexed/non-STEM/unresolved institution):
        # crawl the school's own site, then the affiliation-verified author list.
        if len(pool) < _MIN_FACULTY:
            scraped = await _crawl(client, url, domain, interests, field_terms, robots, deadline, max_pages)
            pool = _dedupe(pool + scraped, university, institution_id)
            if len(pool) < _MIN_FACULTY and institution:
                extra = await _openalex_fallback(client, institution, university, field_terms)
                pool = _dedupe(pool + extra, university, institution_id)
        return pool[:_TARGET_FACULTY]


def _interest_queries(interests: str, key_topics: list[str] | None) -> list[str]:
    """Split typed interests into specific, multi-word OpenAlex queries; optionally
    sharpen the first with a CV research theme (specific phrases cut cross-domain noise)."""
    parts = re.split(r"[;,]|\band\b", interests, flags=re.IGNORECASE)
    queries: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) > 2:
            queries.extend(_TERM_EXPANSIONS.get(p.lower(), [p]))
    if key_topics and queries:
        extra = next((t for t in key_topics if t and t.strip().lower() not in queries[0].lower()), None)
        if extra:
            queries.append(f"{queries[0]} {extra.strip()}")
    return (queries or [interests.strip()])[:6]


async def _openalex_by_field(
    client: httpx.AsyncClient, institution_id: str, university: str,
    interests: str, key_topics: list[str] | None,
) -> list[dict]:
    agg: dict[str, dict] = {}
    for query in _interest_queries(interests, key_topics):
        for a in await openalex.works_by_field(client, institution_id, query):
            cur = agg.get(a["id"])
            if cur is None or a["score"] > cur["_field_score"]:
                agg[a["id"]] = {
                    "name": a["display_name"],
                    "designation": None, "faculty": None, "email": None,
                    "profile_url": a["id"], "listed_interests": [],
                    "university": university, "institution_id": institution_id,
                    "openalex_id": a["id"], "_field_score": a["score"],
                }
    candidates = sorted(agg.values(), key=lambda f: f["_field_score"], reverse=True)

    # Recency gate: drop authors no longer publishing at the institution
    # (affiliation lag / departures). Fall back to ungated if the gate errors/empties.
    since = f"{date.today().year - _RECENCY_YEARS}-01-01"
    try:
        active = await openalex.recent_active_author_ids(
            client, institution_id, [c["openalex_id"] for c in candidates],
            since, _RECENCY_MIN_WORKS,
        )
        gated = [c for c in candidates if c["openalex_id"] in active]
        candidates = gated or candidates
    except Exception:
        pass
    return candidates[:_TARGET_FACULTY]


async def _crawl(client, start, domain, field, field_terms, robots, deadline, max_pages):
    frontier: dict[str, int] = {start: 1000}
    seen: set[str] = set()
    faculty: list[dict] = []
    while frontier and len(seen) < max_pages and time.monotonic() < deadline and len(faculty) < _TARGET_FACULTY:
        url = max(frontier, key=frontier.get)
        del frontier[url]
        if url in seen:
            continue
        seen.add(url)
        if not await _allowed(client, url, robots):
            continue
        try:
            r = await client.get(url, timeout=25)
            r.raise_for_status()
        except Exception:
            continue
        text = trafilatura.extract(r.text) or ""
        page_links = _links(r.text, str(r.url), domain)
        anchor = {u: t for u, t in page_links}
        listing = "\n".join(f"{u} | {t}" for u, t in page_links[:80])
        try:
            res = await generate_json(_PAGE_PROMPT.format(field=field, text=text[:8000], links=listing[:9000]))
        except Exception:
            continue
        for f in (res.get("faculty") or []):
            if f.get("name"):
                faculty.append(f)
        for u in (res.get("next_urls") or []):
            if u not in seen and _same_site(u, domain):
                frontier[u] = max(frontier.get(u, 0), _priority(u, anchor.get(u, ""), field_terms))
    return faculty


async def _resolve_institution(client: httpx.AsyncClient, url: str, domain: str) -> dict | None:
    cached = await cache.get_json(f"inst:{domain}")
    if cached:
        return cached
    # The homepage <title> ("University of Toronto") resolves far more reliably than
    # a domain abbreviation ("utoronto"); the result is still verified by homepage.
    title = await _homepage_title(client, url)
    labels = [l for l in domain.split(".") if l not in _GENERIC and l not in _CCTLDS]
    name_hint = title or (labels[-1].capitalize() if labels else domain)
    queries = [q for q in [title, domain, " ".join(labels), *labels] if q]
    try:
        inst = await openalex.find_institution_by_domain(client, domain, name_hint, queries)
    except Exception:
        return None
    if inst:  # cache only successful resolutions, so a throttle-failure retries
        await cache.set_json(f"inst:{domain}", inst, settings.cache_institution_ttl)
    return inst


async def _homepage_title(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, timeout=15)
        m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.IGNORECASE | re.DOTALL)
        return " ".join(m.group(1).split())[:100] if m else None
    except Exception:
        return None


async def _openalex_fallback(client, institution, university, field_terms) -> list[dict]:
    authors = await openalex.institution_authors(client, institution["id"], limit=50)

    def on_field(a: dict) -> bool:
        if not field_terms:
            return True
        topics = " ".join((t.get("display_name") or "") for t in (a.get("topics") or [])).lower()
        return any(t in topics for t in field_terms)

    picked = [a for a in authors if on_field(a)] or authors[:15]
    return [
        {
            "name": a["display_name"],
            "designation": None,
            "faculty": None,
            "email": None,
            "profile_url": a.get("id"),
            "listed_interests": [
                t.get("display_name") for t in (a.get("topics") or [])[:5] if t.get("display_name")
            ],
            "university": university,
            "openalex_id": a.get("id"),
        }
        for a in picked[:25]
        if a.get("display_name")
    ]


def _dedupe(faculty: list[dict], university: str, institution_id: str | None) -> list[dict]:
    seen, unique = set(), []
    for f in faculty:
        key = (f.get("name") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            f.setdefault("university", university)
            if institution_id:
                f["institution_id"] = institution_id
            unique.append(f)
    return unique


def _registrable_domain(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _university_name(url: str) -> str:
    labels = [l for l in _registrable_domain(url).split(".") if l]
    core = [l for l in labels if l not in _GENERIC and l not in _CCTLDS]
    return core[-1].capitalize() if core else (labels[0].capitalize() if labels else "")


def _field_terms(field: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", field.lower()) if len(w) > 3]


def _priority(url: str, text: str, field_terms: list[str]) -> int:
    s = (url + " " + text).lower()
    # Field relevance dominates so we drill into the student's department first.
    p = 4 * sum(1 for t in field_terms if t in s)
    p += 2 if any(h in s for h in _STAFF_HINTS) else 0
    p += 1 if any(h in s for h in _SECTION_HINTS) else 0
    return p


def _same_site(u: str, domain: str) -> bool:
    h = urlparse(u).netloc.lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == domain or h.endswith("." + domain) or domain.endswith("." + h)


def _links(html: str, base: str, domain: str) -> list[tuple[str, str]]:
    out, seen = [], set()
    for a in HTMLParser(html).css("a"):
        href = a.attributes.get("href") or ""
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        u = urljoin(base, href).split("#")[0]
        if not _same_site(u, domain):
            continue
        if any(u.lower().endswith(e) for e in (".pdf", ".jpg", ".png", ".zip", ".docx", ".xlsx", ".mp4")):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append((u, " ".join(a.text().split())[:80]))
    return out


async def _allowed(client: httpx.AsyncClient, url: str, cache: dict[str, RobotFileParser]) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc
    rp = cache.get(host)
    if rp is None:
        rp = RobotFileParser()
        try:
            r = await client.get(f"{parsed.scheme}://{host}/robots.txt", timeout=10)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                rp.allow_all = True
        except Exception:
            rp.allow_all = True
        cache[host] = rp
    return rp.can_fetch(_UA_TOKEN, url)
