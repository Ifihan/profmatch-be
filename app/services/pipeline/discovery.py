"""Stage 2: LLM-guided faculty discovery — crawl the school's site (robots.txt + time budget), fall back to domain-verified OpenAlex when too few are found."""
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from selectolax.parser import HTMLParser
from app.services.enrichment import openalex
from app.services.gemini import generate_json

_UA = "ProfMatchBot/1.0 (+https://profmatch.example)"
_UA_TOKEN = "ProfMatchBot"
_DEADLINE_SECONDS = 60
_MAX_PAGES = 12
_TARGET_FACULTY = 20
_MIN_FACULTY = 5

_GENERIC = {"ac", "edu", "co", "gov", "org", "com", "net", "sch", "uni", "univ", "www"}
_CCTLDS = {"za", "uk", "au", "ng", "ke", "gh", "in", "pk", "my", "sg", "nz", "ca", "us",
           "ie", "rs", "hr", "de", "fr", "es", "it", "nl", "se", "no", "fi", "dk", "ch",
           "at", "be", "pt", "gr", "pl", "cz", "br", "mx", "ar", "cl", "jp", "cn", "kr",
           "tr", "eg", "sa", "ae"}
_STAFF_HINTS = ("staff", "people", "academic", "faculty", "professor", "lecturer",
                "researcher", "member", "team", "directory")
_SECTION_HINTS = ("school", "department", "dept", "institute", "faculty of", "centre", "center")

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


async def run(university_url: str, field: str, max_pages: int = _MAX_PAGES) -> list[dict]:
    url = str(university_url)
    domain = _registrable_domain(url)
    field_terms = _field_terms(field)
    deadline = time.monotonic() + _DEADLINE_SECONDS
    robots: dict[str, RobotFileParser] = {}

    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": _UA}) as client:
        institution = await _resolve_institution(client, domain)
        institution_id = institution["id"] if institution else None
        university = institution["display_name"] if institution else _university_name(url)

        faculty = await _crawl(client, url, domain, field, field_terms, robots, deadline, max_pages)
        unique = _dedupe(faculty, university, institution_id)

        # Too few from the school's own site — fall back to its OpenAlex authors.
        if len(unique) < _MIN_FACULTY and institution and time.monotonic() < deadline:
            extra = await _openalex_fallback(client, institution, university, field_terms)
            unique = _dedupe(unique + extra, university, institution_id)
        return unique


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


async def _resolve_institution(client: httpx.AsyncClient, domain: str) -> dict | None:
    labels = [l for l in domain.split(".") if l not in _GENERIC and l not in _CCTLDS]
    name_hint = labels[-1].capitalize() if labels else domain
    queries = [domain, " ".join(labels), *labels]
    try:
        return await openalex.find_institution_by_domain(client, domain, name_hint, queries)
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
