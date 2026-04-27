from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.models import CitationMetrics, MatchResult, ProfessorProfile, Publication
from app.services import gemini, openalex, tools
from app.services.cache import (
    cache_professor,
    get_cached_professor,
    get_cached_professor_by_openalex_id,
    get_cached_professors_batch,
)
from app.services.matching.routing import _extract_domain, _normalize_title

logger = logging.getLogger(__name__)

_AUTHOR_WORK_LIMIT = 8
_AUTHOR_LOOKBACK_YEARS = 5
_ENRICHMENT_CONCURRENCY = 12


async def enrich_single_professor(
    *,
    faculty: dict[str, Any],
    university: str,
    university_url: str,
    all_works: dict[str, list[dict]] | None = None,
) -> ProfessorProfile | None:
    """Enrich a single professor profile with publication and citation data."""
    name = faculty.get("name", "")
    if not name:
        return None

    openalex_id = faculty.get("openalex_id")
    if openalex_id:
        if cached := await get_cached_professor_by_openalex_id(openalex_id=openalex_id):
            return cached
    if cached := await get_cached_professor(name=name, university=university):
        return cached

    if openalex_id:
        return await _enrich_from_openalex(
            faculty=faculty,
            university=university,
            pre_fetched_works=all_works.get(openalex_id) if all_works else None,
        )

    return await _enrich_from_scraping(
        faculty=faculty,
        university=university,
        university_url=university_url,
    )


async def _enrich_from_openalex(
    *,
    faculty: dict[str, Any],
    university: str,
    pre_fetched_works: list[dict] | None = None,
) -> ProfessorProfile:
    """Enrich professor data from OpenAlex."""
    openalex_id = faculty["openalex_id"]
    works = pre_fetched_works
    if works is None:
        works = await openalex.get_author_works(
            author_id=openalex_id,
            limit=_AUTHOR_WORK_LIMIT,
            years=_AUTHOR_LOOKBACK_YEARS,
        )

    publications = [
        Publication(
            title=work.get("title", ""),
            authors=work.get("authors", []),
            year=work.get("publication_year", 0),
            venue=work.get("venue"),
            abstract=work.get("abstract"),
            citation_count=work.get("citation_count", 0),
            url=work.get("doi"),
        )
        for work in works
    ]

    citation_metrics = CitationMetrics(
        h_index=faculty.get("h_index", 0),
        i10_index=faculty.get("i10_index", 0),
        total_citations=faculty.get("cited_by_count", 0),
    )

    profile = ProfessorProfile(
        id=uuid4(),
        name=faculty["name"],
        title=_normalize_title(faculty.get("title")),
        department=faculty.get("department"),
        university=university,
        email=faculty.get("email"),
        openalex_id=openalex_id,
        directory_url=faculty.get("profile_url"),
        website=faculty.get("profile_url"),
        research_areas=_clean_research_areas(faculty.get("topics", [])[:7]),
        publications=publications,
        citation_metrics=citation_metrics,
        last_updated=datetime.now(UTC),
    )
    await cache_professor(profile=profile)
    return profile


async def _enrich_from_scraping(
    *,
    faculty: dict[str, Any],
    university: str,
    university_url: str,
) -> ProfessorProfile:
    """Enrich scraped faculty using profile parsing and OpenAlex lookup."""
    name = faculty["name"]
    profile_url = faculty.get("profile_url")
    extra_data: dict[str, Any] = {}

    if profile_url:
        try:
            page_content = await tools.fetch_page_content(url=profile_url)
            details = await gemini.extract_professor_details(page_content=page_content)
            extra_data = details.model_dump()
        except Exception:
            pass

    publications: list[Publication] = []
    citation_metrics: CitationMetrics | None = None
    openalex_id: str | None = None
    openalex_topics: list[str] = []

    try:
        institution = await openalex.resolve_institution(query=university_url or university)
        institution_id = institution["id"] if institution else None
        author = await openalex.search_author_by_name(name=name, institution_id=institution_id)
        if author:
            openalex_id = author["openalex_id"]
            openalex_topics = author.get("topics", [])[:7]
            citation_metrics = CitationMetrics(
                h_index=author.get("h_index", 0),
                i10_index=author.get("i10_index", 0),
                total_citations=author.get("cited_by_count", 0),
            )
            works = await openalex.get_author_works(
                author_id=openalex_id,
                limit=_AUTHOR_WORK_LIMIT,
                years=_AUTHOR_LOOKBACK_YEARS,
            )
            publications = [
                Publication(
                    title=work.get("title", ""),
                    authors=work.get("authors", []),
                    year=work.get("publication_year", 0),
                    venue=work.get("venue"),
                    abstract=work.get("abstract"),
                    citation_count=work.get("citation_count", 0),
                    url=work.get("doi"),
                )
                for work in works
            ]
    except Exception as exc:
        logger.debug("OpenAlex name lookup failed for %s: %s", name, exc)

    research_areas = extra_data.get("research_areas") or []
    if isinstance(research_areas, str):
        research_areas = [area.strip() for area in research_areas.split(",") if area.strip()]
    if openalex_topics:
        research_areas = openalex_topics
    research_areas = _clean_research_areas(research_areas)

    profile = ProfessorProfile(
        id=uuid4(),
        name=name,
        title=_normalize_title(faculty.get("title") or extra_data.get("title")),
        department=faculty.get("department") or extra_data.get("department"),
        university=university,
        email=faculty.get("email") or extra_data.get("email"),
        openalex_id=openalex_id,
        directory_url=profile_url,
        website=profile_url,
        research_areas=research_areas,
        publications=publications,
        citation_metrics=citation_metrics,
        last_updated=datetime.now(UTC),
    )
    await cache_professor(profile=profile)
    return profile


def _clean_research_areas(areas: list[str]) -> list[str]:
    """Filter out low-quality research area entries."""
    stopwords = {
        "the", "of", "and", "for", "a", "an", "in", "to", "on", "with",
        "is", "are", "was", "were", "be", "been", "being",
    }
    cleaned = []
    for area in areas:
        if not area or not isinstance(area, str):
            continue
        area = area.strip()
        if len(area) < 3 or area.lower() in stopwords:
            continue
        cleaned.append(area)
    return cleaned


async def enrich_professors(
    *,
    faculty_data: list[dict[str, Any]],
    university: str,
    university_url: str,
) -> tuple[list[ProfessorProfile], list[dict]]:
    """Enrich professor profiles with publication data."""
    lookups = [
        (faculty.get("openalex_id"), faculty.get("name", ""), university)
        for faculty in faculty_data
    ]
    cached_map = await get_cached_professors_batch(lookups=lookups)

    professors: list[ProfessorProfile] = []
    uncached_faculty: list[dict[str, Any]] = []
    for faculty in faculty_data:
        openalex_id = faculty.get("openalex_id")
        name = faculty.get("name", "")
        cached = cached_map.get(openalex_id) if openalex_id else None
        if not cached:
            cached = cached_map.get(f"{name}|{university}")
        if cached:
            professors.append(cached)
        else:
            uncached_faculty.append(faculty)

    openalex_ids = [
        faculty["openalex_id"]
        for faculty in uncached_faculty
        if faculty.get("openalex_id")
    ]
    all_works: dict[str, list[dict]] = {}
    if openalex_ids:
        try:
            all_works = await openalex.get_works_for_authors(
                author_ids=openalex_ids,
                limit_per_author=_AUTHOR_WORK_LIMIT,
                years=_AUTHOR_LOOKBACK_YEARS,
            )
        except Exception as exc:
            logger.warning("batch works fetch failed, falling back to individual: %s", exc)

    semaphore = asyncio.Semaphore(_ENRICHMENT_CONCURRENCY)

    async def enrich_with_limit(faculty: dict[str, Any]) -> ProfessorProfile | None:
        async with semaphore:
            return await enrich_single_professor(
                faculty=faculty,
                university=university,
                university_url=university_url,
                all_works=all_works,
            )

    results = await asyncio.gather(
        *(enrich_with_limit(faculty) for faculty in uncached_faculty),
        return_exceptions=True,
    )

    errors = []
    for faculty, result in zip(uncached_faculty, results):
        if isinstance(result, ProfessorProfile):
            professors.append(result)
        elif isinstance(result, Exception):
            errors.append({
                "name": faculty.get("name", "unknown"),
                "error_type": type(result).__name__,
                "error_message": str(result),
            })

    _deduplicate_research_areas(professors)
    return professors, errors


def _deduplicate_research_areas(professors: list[ProfessorProfile]) -> None:
    """Detect and clear page-level research-area noise."""
    if len(professors) < 3:
        return

    area_counts: Counter[str] = Counter()
    for professor in professors:
        if professor.research_areas:
            key = "|".join(sorted(area.lower() for area in professor.research_areas))
            area_counts[key] += 1

    noise_keys = {key for key, count in area_counts.items() if count >= 3}
    if not noise_keys:
        return

    logger.info(
        "detected %d noise research area patterns, clearing affected professors",
        len(noise_keys),
    )
    for professor in professors:
        if professor.research_areas:
            key = "|".join(sorted(area.lower() for area in professor.research_areas))
            if key in noise_keys:
                professor.research_areas = []


async def post_enrich_matches(*, matches: list[MatchResult], university_url: str) -> None:
    """Fetch supplementary data for matched professors only."""
    if not matches:
        return

    domain = _extract_domain(university_url) if university_url else ""

    async def enrich_one(match: MatchResult) -> None:
        professor = match.professor
        tasks = {}
        if not professor.google_scholar_url:
            tasks["scholar"] = tools.search_google_scholar_url(
                name=professor.name,
                university=professor.university,
            )
        if domain and (not professor.email or not professor.directory_url):
            tasks["contact"] = tools.search_professor_contact(
                name=professor.name,
                university_domain=domain,
            )
        if not tasks:
            return

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        result_map = dict(zip(tasks.keys(), results))

        scholar_url = result_map.get("scholar")
        if isinstance(scholar_url, str):
            professor.google_scholar_url = scholar_url

        contact_info = result_map.get("contact")
        if isinstance(contact_info, dict):
            if not professor.email:
                professor.email = contact_info.get("email")
            if not professor.directory_url:
                professor.directory_url = contact_info.get("homepage")

    await asyncio.gather(*(enrich_one(match) for match in matches), return_exceptions=True)
