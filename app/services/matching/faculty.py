from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from app.services import gemini, openalex, tools
from app.services.cache import cache_faculty, get_cached_faculty
from app.services.matching.interests import ResearchInterestProfile
from app.services.matching.routing import (
    _compute_topic_score,
    _directory_search_terms,
    _infer_matching_route,
    _infer_target_academic_units,
    _merge_faculty_sources,
    _normalize_text,
)

logger = logging.getLogger(__name__)

_DIRECTORY_METADATA_TIMEOUT_SECONDS = 12
_SHORTLIST_CACHE_TTL_SECONDS = 6 * 60 * 60
_search_result_cache: dict[str, tuple[float, list[dict[str, Any]], str | None]] = {}
_DISALLOWED_FILE_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def _search_cache_key(*, university: str, research_interests: list[str]) -> str:
    route_name = _infer_matching_route(research_interests)
    normalized_interests = sorted(
        {
            " ".join(term.split())
            for term in (
                interest.strip().lower() for interest in research_interests
            )
            if term
        }
    )
    return f"{university.strip().lower()}|{route_name}|{'|'.join(normalized_interests)}"


def _allowed_university_hosts(university: str) -> set[str]:
    parsed = urlparse(university if university.startswith(("http://", "https://")) else f"https://{university}")
    host = parsed.netloc.lower()
    if not host:
        return set()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    hosts = {host}
    if len(parts) >= 2:
        hosts.add(".".join(parts[-2:]))
    return hosts


def _is_private_host(host: str) -> bool:
    host = host.strip().lower()
    if not host:
        return True
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        return False


def _is_allowed_directory_url(*, url: str, allowed_hosts: set[str]) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False

    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or _is_private_host(host):
        return False
    if allowed_hosts and not any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
        return False

    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in _DISALLOWED_FILE_EXTENSIONS):
        return False
    return True


def _score_author_relevance(
    *,
    author: dict[str, Any],
    research_interests: list[str],
    route_name: str,
    target_units: list[str],
) -> float:
    interest_tokens = {
        token
        for interest in research_interests
        for token in _normalize_text(interest).split()
        if len(token) > 2
    }
    interest_phrases = [_normalize_text(interest) for interest in research_interests]
    return _compute_topic_score(
        faculty={
            "topics": author.get("topics", []),
            "topic_details": author.get("topic_details", []),
            "department": None,
            "directory_verified": False,
        },
        interest_tokens=interest_tokens,
        interest_phrases=interest_phrases,
        target_units=target_units,
        route_name=route_name,
    )


def _get_cached_search_result(
    *,
    university: str,
    research_interests: list[str],
) -> tuple[list[dict[str, Any]], str | None] | None:
    cache_key = _search_cache_key(
        university=university,
        research_interests=research_interests,
    )
    cached = _search_result_cache.get(cache_key)
    if not cached:
        return None

    cached_at, faculty_data, institution_name = cached
    if time.time() - cached_at > _SHORTLIST_CACHE_TTL_SECONDS:
        _search_result_cache.pop(cache_key, None)
        return None

    return deepcopy(faculty_data), institution_name


def _cache_search_result(
    *,
    university: str,
    research_interests: list[str],
    faculty_data: list[dict[str, Any]],
    institution_name: str | None,
) -> None:
    cache_key = _search_cache_key(
        university=university,
        research_interests=research_interests,
    )
    _search_result_cache[cache_key] = (
        time.time(),
        deepcopy(faculty_data),
        institution_name,
    )


async def fetch_faculty(
    *,
    university: str,
    research_interests: list[str],
    research_profile: ResearchInterestProfile | None = None,
) -> tuple[list[dict[str, Any]], list[dict], str | None]:
    """Fetch faculty using OpenAlex plus targeted directory metadata when possible."""
    if cached := _get_cached_search_result(
        university=university,
        research_interests=research_profile.normalized_phrases if research_profile else research_interests,
    ):
        faculty_data, institution_name = cached
        return faculty_data, [], institution_name

    warnings: list[dict] = []
    institution_name: str | None = None

    openalex_task = _fetch_faculty_openalex(
        university=university,
        research_interests=research_interests,
        research_profile=research_profile,
    )
    scraping_task = asyncio.wait_for(
        _fetch_faculty_fallback(
            university=university,
            research_interests=research_interests,
            research_profile=research_profile,
        ),
        timeout=_DIRECTORY_METADATA_TIMEOUT_SECONDS,
    )

    openalex_result, scraping_result = await asyncio.gather(
        openalex_task,
        scraping_task,
        return_exceptions=True,
    )

    openalex_faculty: list[dict[str, Any]] = []
    scraped_faculty: list[dict[str, Any]] = []

    if isinstance(openalex_result, Exception):
        warnings.append({"stage": "openalex_discovery", "error": str(openalex_result)})
    else:
        openalex_faculty, openalex_warnings, institution_name = openalex_result
        warnings.extend(openalex_warnings)

    if isinstance(scraping_result, Exception):
        warnings.append({"stage": "scraping_fallback", "error": str(scraping_result)})
    else:
        scraped_faculty, scraping_warnings = scraping_result
        warnings.extend(scraping_warnings)

    if openalex_faculty and scraped_faculty:
        merged = _merge_faculty_sources(
            openalex_faculty=openalex_faculty,
            scraped_faculty=scraped_faculty,
        )
        if merged:
            _cache_search_result(
                university=university,
                research_interests=research_profile.normalized_phrases if research_profile else research_interests,
                faculty_data=merged,
                institution_name=institution_name,
            )
            return merged, warnings, institution_name

    if openalex_faculty:
        _cache_search_result(
            university=university,
            research_interests=research_profile.normalized_phrases if research_profile else research_interests,
            faculty_data=openalex_faculty,
            institution_name=institution_name,
        )
        return openalex_faculty, warnings, institution_name
    if scraped_faculty:
        _cache_search_result(
            university=university,
            research_interests=research_profile.normalized_phrases if research_profile else research_interests,
            faculty_data=scraped_faculty,
            institution_name=institution_name,
        )
        return scraped_faculty, warnings, institution_name
    return [], warnings, institution_name


async def _fetch_faculty_openalex(
    *,
    university: str,
    research_interests: list[str],
    research_profile: ResearchInterestProfile | None = None,
) -> tuple[list[dict[str, Any]], list[dict], str | None]:
    """Discover faculty via OpenAlex institution + author search."""
    warnings: list[dict] = []
    route_name = research_profile.route_name if research_profile else _infer_matching_route(research_interests)
    effective_interests = (
        research_profile.normalized_phrases
        if research_profile and research_profile.normalized_phrases
        else research_interests
    )
    target_units = (
        research_profile.target_units
        if research_profile and research_profile.target_units
        else _infer_target_academic_units(effective_interests, route_name)
    )

    institution = await openalex.resolve_institution(query=university)
    if not institution:
        warnings.append({
            "stage": "openalex_institution",
            "error": f"could not resolve institution: {university}",
        })
        return [], warnings, None

    institution_id = institution["id"]
    institution_name = institution["display_name"]
    logger.info("resolved institution: %s (id=%s)", institution_name, institution_id)

    authors = await openalex.get_authors_by_institution(
        institution_id=institution_id,
        topics=(research_profile.normalized_phrases if research_profile else research_interests),
        limit=120,
    )

    if len(authors) < 10:
        logger.info(
            "topic-filtered search returned only %d authors, retrying without topic filter",
            len(authors),
        )
        broader_authors = await openalex.get_authors_by_institution(
            institution_id=institution_id,
            topics=None,
            limit=120,
        )
        seen_ids = {author["openalex_id"] for author in authors}
        for author in broader_authors:
            if author["openalex_id"] not in seen_ids:
                authors.append(author)
                seen_ids.add(author["openalex_id"])

    if not authors:
        warnings.append({"stage": "openalex_authors", "error": "no authors found for institution"})
        return [], warnings, institution_name

    verified_authors = []
    for author in authors:
        institutions = author.get("last_known_institutions", [])
        if not institutions:
            continue
        primary_id = institutions[0].get("id", "")
        if primary_id == institution_id:
            verified_authors.append(author)
        elif any(inst.get("id") == institution_id for inst in institutions):
            verified_authors.append(author)

    if not verified_authors and authors:
        logger.warning(
            "institution verification filtered all %d authors, using unfiltered",
            len(authors),
        )
        verified_authors = authors

    scored_authors = [
        (
            author,
            _score_author_relevance(
                author=author,
                research_interests=effective_interests,
                route_name=route_name,
                target_units=target_units,
            ),
        )
        for author in verified_authors
    ]
    positive_authors = [(author, score) for author, score in scored_authors if score > 0]

    # Log filtering stats
    if scored_authors:
        score_values = [s for _, s in scored_authors]
        logger.info(
            f"Author relevance scores: {len(positive_authors)} positive (>0) / {len(scored_authors)} total. "
            f"Range: {min(score_values, default=0):.2f} - {max(score_values, default=0):.2f}. "
            f"Route: {route_name}, Units: {target_units}"
        )

    if positive_authors:
        positive_authors.sort(
            key=lambda item: (item[1], item[0].get("cited_by_count", 0)),
            reverse=True,
        )
        verified_authors = [author for author, _ in positive_authors[:120]]
    else:
        scored_authors.sort(
            key=lambda item: (item[1], item[0].get("cited_by_count", 0)),
            reverse=True,
        )
        verified_authors = [author for author, _ in scored_authors[:120]]

    faculty = [
        {
            "name": author["name"],
            "title": None,
            "department": None,
            "email": None,
            "profile_url": None,
            "directory_verified": False,
            "openalex_id": author["openalex_id"],
            "topics": author["topics"],
            "topic_details": author["topic_details"],
            "h_index": author["h_index"],
            "i10_index": author["i10_index"],
            "cited_by_count": author["cited_by_count"],
            "works_count": author["works_count"],
            "orcid": author["orcid"],
        }
        for author in verified_authors
    ]

    logger.info(
        "OpenAlex discovery: %d authors found, %d after verification",
        len(authors),
        len(faculty),
    )
    return faculty, warnings, institution_name


async def _discover_faculty_url(*, university: str, query_term: str) -> list[str]:
    """Discover faculty directory URL(s) from an academic-unit or interest query."""
    if not university.startswith(("http://", "https://")):
        university = "https://" + university
    parsed = urlparse(university)

    explicit_keywords = ["faculty", "staff", "people", "directory", "team", "professors"]
    if any(keyword in parsed.path.lower() for keyword in explicit_keywords):
        return [university]

    domain = parsed.netloc.replace("www.", "")
    query = f'site:{domain} "{query_term.strip() or "faculty"}" faculty directory'
    urls = await tools.search_web(query=query)
    return urls if urls else [university]


async def _fetch_faculty_fallback(
    *,
    university: str,
    research_interests: list[str],
    research_profile: ResearchInterestProfile | None = None,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Fallback faculty discovery via search + page extraction + Gemini."""
    route_name = research_profile.route_name if research_profile else _infer_matching_route(research_interests)
    allowed_hosts = _allowed_university_hosts(university)
    search_terms = (
        research_profile.discovery_terms
        if research_profile and research_profile.discovery_terms
        else _directory_search_terms(
            research_interests=research_interests,
            route_name=route_name,
        )
    ) or ["faculty"]

    discovery_results = await asyncio.gather(
        *(
            _discover_faculty_url(university=university, query_term=term)
            for term in search_terms[:2]
        ),
        return_exceptions=True,
    )

    seen_urls: set[str] = set()
    search_pairs: list[tuple[str, str]] = []
    warnings: list[dict] = []
    for term, result in zip(search_terms[:2], discovery_results):
        if isinstance(result, Exception):
            warnings.append({"stage": "url_discovery", "interest": term, "error": str(result)})
            continue
        for url in result:
            if not _is_allowed_directory_url(url=url, allowed_hosts=allowed_hosts):
                warnings.append({
                    "stage": "url_discovery",
                    "interest": term,
                    "url": url,
                    "error": "rejected non-university or non-html url",
                })
                continue
            if url not in seen_urls:
                seen_urls.add(url)
                search_pairs.append((url, term))

    async def search_one(url: str, interest: str) -> list[dict[str, Any]]:
        cached = await get_cached_faculty(source_url=url)
        if cached is not None:
            faculty_dicts = cached
        else:
            faculty_url = url
            faculty_keywords = ["faculty", "staff", "people", "directory", "team", "professors"]

            if not any(keyword in url.lower().split("?")[0] for keyword in faculty_keywords):
                try:
                    page_content = await tools.fetch_page_content(url=url)
                    found_url = await gemini.find_faculty_directory_url(
                        page_content=page_content,
                        base_url=url,
                    )
                    if found_url and _is_allowed_directory_url(
                        url=found_url,
                        allowed_hosts=allowed_hosts,
                    ):
                        faculty_url = found_url
                    else:
                        warnings.append({
                            "stage": "directory_discovery",
                            "url": url,
                            "error": "no faculty directory found",
                        })
                        return []
                except Exception as exc:
                    warnings.append({
                        "stage": "directory_discovery",
                        "url": url,
                        "error": str(exc),
                    })
                    return []

            try:
                page_content = await tools.fetch_page_content(url=faculty_url)
                members = await gemini.extract_faculty(page_content=page_content, url=faculty_url)
                faculty_dicts = [member.model_dump() for member in members]
                await cache_faculty(
                    source_url=url,
                    university=university,
                    members=faculty_dicts,
                )
            except Exception as exc:
                warnings.append({"stage": "faculty_extraction", "url": url, "error": str(exc)})
                return []

        stopwords = {
            "in", "the", "of", "and", "for", "a", "an",
            "to", "on", "with", "field", "area", "using", "based",
        }
        keywords = [
            token.lower().strip(".,;:()")
            for token in interest.split()
            if token.lower().strip(".,;:()") not in stopwords and len(token.strip(".,;:()")) > 2
        ]

        scored = []
        for faculty in faculty_dicts:
            text = " ".join([
                faculty.get("name") or "",
                faculty.get("title") or "",
                faculty.get("department") or "",
            ]).lower()
            score = sum(1 for keyword in keywords if keyword in text)
            scored.append((faculty, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return [faculty for faculty, _ in scored if isinstance(faculty, dict) and faculty.get("name")]

    fetch_results = await asyncio.gather(
        *(search_one(url, interest) for url, interest in search_pairs),
        return_exceptions=True,
    )

    all_faculty: list[dict[str, Any]] = []
    for result in fetch_results:
        if isinstance(result, list):
            all_faculty.extend(result)

    seen_names: set[str] = set()
    unique_faculty = []
    for faculty in all_faculty:
        name = faculty.get("name", "")
        if name and name not in seen_names:
            seen_names.add(name)
            unique_faculty.append(faculty)

    return unique_faculty, warnings
