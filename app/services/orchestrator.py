import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any
from uuid import uuid4
from urllib.parse import urlparse

from app.models import (
    CitationMetrics,
    Education,
    Experience,
    MatchResult,
    ProfessorProfile,
    Publication,
    StudentProfile,
)
from app.services.cache import cache_professor, get_cached_professor
from app.services import gemini, tools
from app.services.api_cache import get_cached, set_cached
from app.services.redis import get_session, set_session
from app.utils.storage import get_file_path

logger = logging.getLogger(__name__)


async def update_progress(session_id: str, progress: int, step: str) -> None:
    """Update matching progress in session."""
    session = await get_session(session_id)
    if session:
        session["match_progress"] = progress
        session["current_step"] = step
        await set_session(session_id, session)


async def run_matching(
    session_id: str,
    university: str,
    research_interests: list[str],
    file_ids: list[str] | None = None,
) -> list[MatchResult]:
    """Run the full matching pipeline."""
    # Wide event: one structured log per matching request
    wide_event: dict[str, Any] = {
        "event": "matching_pipeline",
        "session_id": session_id,
        "university": university,
        "research_interests": research_interests,
        "file_count": len(file_ids) if file_ids else 0,
        "start_time": time.time(),
    }

    try:
        await update_progress(session_id, 5, "Parsing uploaded documents")

        student_profile = None
        if file_ids:
            student_profile = await parse_student_documents(
                session_id, file_ids, research_interests
            )
            wide_event["student_profile_parsed"] = True

        await update_progress(session_id, 15, "Fetching faculty directory")

        faculty_data = await fetch_faculty(university, research_interests)
        wide_event["faculty_found"] = len(faculty_data)

        await update_progress(session_id, 25, "Filtering candidates")

        faculty_data = await filter_faculty_by_relevance(
            faculty_data, research_interests
        )
        wide_event["faculty_filtered"] = len(faculty_data)

        await update_progress(session_id, 30, "Retrieving publication data")

        professors = await enrich_professors(faculty_data, university)
        wide_event["professors_enriched"] = len(professors)

        await update_progress(session_id, 70, "Analyzing research alignment")

        matches = await generate_matches(
            professors, research_interests, student_profile
        )
        wide_event["matches_generated"] = len(matches)

        await update_progress(session_id, 90, "Fetching citation metrics")

        await enrich_matches_with_google_scholar(matches, university)

        await update_progress(session_id, 95, "Finalizing recommendations")

        session = await get_session(session_id)
        if session:
            start_time = session.get("match_start_time")
            if start_time:
                total_time = time.time() - start_time
                session["total_match_time"] = total_time
                wide_event["total_time_seconds"] = total_time

            session["match_status"] = "completed"
            session["match_progress"] = 100
            session["current_step"] = "Complete"
            session["match_results"] = [
                m.model_dump(mode="json") for m in matches
            ]
            await set_session(session_id, session)

        wide_event["outcome"] = "success"
        wide_event["status_code"] = 200
        return matches

    except Exception as e:
        wide_event["outcome"] = "error"
        wide_event["error"] = {"message": str(e), "type": type(e).__name__}
        raise

    finally:
        wide_event["duration_ms"] = int(
            (time.time() - wide_event["start_time"]) * 1000
        )
        logger.info(json.dumps(wide_event, default=str))


def to_str(val) -> str | None:
    """Convert value to string, joining lists if needed."""
    if val is None:
        return None
    if isinstance(val, list):
        return "; ".join(str(v) for v in val)
    return str(val)


def to_list(val) -> list[str]:
    """Convert value to list, splitting strings if needed."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [v.strip() for v in val.split(",")]
    return [str(val)]


def to_int(val) -> int:
    """Convert value to int safely."""
    if val is None:
        return 0
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


async def parse_student_documents(
    session_id: str,
    file_ids: list[str],
    research_interests: list[str],
) -> StudentProfile:
    """Parse uploaded documents to build student profile."""
    education = []
    experience = []
    publications = []
    skills = []
    extracted_keywords = list(research_interests)

    async def parse_single_file(file_id: str) -> dict | None:
        file_path = await get_file_path(session_id, file_id)
        if not file_path:
            return None
        # Step 1: Extract raw text (no LLM)
        text = tools.extract_text_from_file(str(file_path))
        # Step 2: Use Gemini structured output to parse CV
        parsed = await gemini.parse_cv(text)
        return parsed.model_dump()

    tasks = [parse_single_file(fid) for fid in file_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if not isinstance(result, dict):
            continue
        cv_data = result

        for edu in cv_data.get("education", []) or []:
            if not isinstance(edu, dict):
                continue
            education.append(
                Education(
                    institution=to_str(edu.get("institution")) or "",
                    degree=to_str(edu.get("degree")) or "",
                    field=to_str(edu.get("field")),
                    year=to_int(edu.get("year")) or None,
                )
            )

        for exp in cv_data.get("experience", []) or []:
            if not isinstance(exp, dict):
                continue
            experience.append(
                Experience(
                    organization=to_str(exp.get("organization")) or "",
                    role=to_str(exp.get("role")) or "",
                    description=to_str(exp.get("description")),
                    start_year=to_int(exp.get("start_year")) or None,
                    end_year=to_int(exp.get("end_year")) or None,
                )
            )

        for pub in cv_data.get("publications", []) or []:
            if not isinstance(pub, dict):
                continue
            publications.append(
                Publication(
                    title=to_str(pub.get("title")) or "",
                    authors=to_list(pub.get("authors")),
                    year=to_int(pub.get("year")),
                    venue=to_str(pub.get("venue")),
                )
            )

        raw_skills = cv_data.get("skills", [])
        if isinstance(raw_skills, list):
            skills.extend(str(s) for s in raw_skills if s)
        elif raw_skills:
            skills.extend(to_list(raw_skills))

        raw_interests = cv_data.get("research_interests", [])
        if isinstance(raw_interests, list):
            extracted_keywords.extend(str(i) for i in raw_interests if i)
        elif raw_interests:
            extracted_keywords.extend(to_list(raw_interests))

    return StudentProfile(
        session_id=uuid4(),
        stated_interests=research_interests,
        education=education,
        experience=experience,
        publications=publications,
        skills=list(set(skills)),
        extracted_keywords=list(set(extracted_keywords)),
    )


def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        domain = urlparse(url).netloc
        return domain.replace("www.", "").split("/")[0]
    except Exception:
        return ""


async def discover_faculty_url(university: str, interest: str) -> list[str]:
    """Discover specific faculty directory URL(s) via web search if generic."""
    if not university.startswith(("http://", "https://")):
        university = "https://" + university
    parsed = urlparse(university)

    path = parsed.path.lower()
    explicit_keywords = [
        "faculty",
        "staff",
        "people",
        "directory",
        "team",
        "professors",
    ]

    if any(k in path for k in explicit_keywords):
        return [university]

    domain = parsed.netloc.replace("www.", "")
    query = f"Computer Science faculty directory {domain}"
    if interest:
        query = f"{interest} faculty directory {domain}"

    # Check cache first
    cached = await get_cached("search", query)
    if cached and isinstance(cached, list):
        return cached

    urls = await tools.search_web(query)
    if urls:
        await set_cached("search", query, data=urls, ttl_days=7)
        return urls

    return [university]


async def fetch_faculty(
    university: str, research_interests: list[str]
) -> list[dict[str, Any]]:
    """Fetch faculty from university directory."""
    discovery_tasks = [
        discover_faculty_url(university, interest)
        for interest in research_interests[:3]
    ]
    discovery_results = await asyncio.gather(
        *discovery_tasks, return_exceptions=True
    )

    # Build (url, interest) pairs, deduplicating URLs
    seen_urls: set[str] = set()
    search_pairs: list[tuple[str, str]] = []
    for interest, result in zip(research_interests[:3], discovery_results):
        if isinstance(result, Exception):
            logger.warning(
                f"Faculty URL discovery failed for '{interest}': {result}"
            )
            continue
        for url in result:
            if url not in seen_urls:
                seen_urls.add(url)
                search_pairs.append((url, interest))

    async def search_one(url: str, interest: str) -> list[dict]:
        logger.info(f"Searching faculty for interest: {interest} at {url}")

        # Check cache for this faculty page
        cached = await get_cached("faculty_page", url)
        if cached and isinstance(cached, list):
            logger.info(f"Cache hit for faculty page: {url}")
            faculty_dicts = cached
        else:
            # Determine if URL is already a faculty directory
            faculty_keywords = [
                "faculty",
                "staff",
                "people",
                "directory",
                "team",
                "professors",
            ]
            path_lower = url.lower().split("?")[0]
            faculty_url = url

            if not any(k in path_lower for k in faculty_keywords):
                # Need to discover the faculty directory first
                try:
                    page_content = await tools.fetch_page_content(url)
                    found_url = await gemini.find_faculty_directory_url(
                        page_content, url
                    )
                    if found_url:
                        faculty_url = found_url
                    else:
                        logger.warning(
                            f"Could not find faculty directory at {url}"
                        )
                        return []
                except Exception as e:
                    logger.warning(f"Faculty directory discovery failed: {e}")
                    return []

            # Fetch and parse the faculty page
            try:
                page_content = await tools.fetch_page_content(faculty_url)
                members = await gemini.extract_faculty(
                    page_content, faculty_url
                )
                faculty_dicts = [m.model_dump() for m in members]
                # Cache the parsed faculty list
                await set_cached(
                    "faculty_page", url, data=faculty_dicts, ttl_days=7
                )
            except Exception as e:
                logger.warning(f"Faculty extraction failed at {url}: {e}")
                return []

        # Keyword-based pre-filtering (same scoring as university-server)
        stopwords = {
            "in",
            "the",
            "of",
            "and",
            "for",
            "a",
            "an",
            "to",
            "on",
            "with",
            "field",
            "area",
            "using",
            "based",
        }
        keywords = [
            w.lower().strip(".,;:()")
            for w in interest.split()
            if w.lower().strip(".,;:()") not in stopwords
            and len(w.strip(".,;:()")) > 2
        ]

        scored = []
        for f in faculty_dicts:
            text = " ".join(
                [
                    (f.get("name") or ""),
                    (f.get("title") or ""),
                    (f.get("department") or ""),
                ]
            ).lower()
            score = sum(1 for kw in keywords if kw in text)
            scored.append((f, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        sorted_faculty = [f for f, _ in scored]

        valid = [f for f in sorted_faculty if isinstance(f, dict) and f.get("name")]
        logger.info(f"Found {len(valid)} faculty members at {url}")
        return valid

    fetch_tasks = [search_one(url, interest) for url, interest in search_pairs]
    fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    all_faculty = []
    for result in fetch_results:
        if isinstance(result, list):
            all_faculty.extend(result)

    seen_names: set[str] = set()
    unique_faculty = []
    for f in all_faculty:
        name = f.get("name", "")
        if name and name not in seen_names:
            seen_names.add(name)
            unique_faculty.append(f)

    logger.info(f"Total unique faculty found: {len(unique_faculty)}")
    return unique_faculty


async def filter_faculty_by_relevance(
    faculty_data: list[dict[str, Any]],
    research_interests: list[str],
) -> list[dict[str, Any]]:
    """Use LLM to filter faculty list to the most relevant candidates."""
    if len(faculty_data) <= 30:
        return faculty_data

    interests_str = ", ".join(research_interests)

    summaries = []
    for i, f in enumerate(faculty_data):
        parts = [f"[{i}] {f.get('name', 'Unknown')}"]
        if f.get("title"):
            parts.append(f"- {f['title']}")
        if f.get("department"):
            parts.append(f"({f['department']})")
        summaries.append(" ".join(parts))

    faculty_list = "\n".join(summaries)

    try:
        result = await gemini.filter_faculty(faculty_list, interests_str)
        indices = result.selected_indices
        selected = [
            faculty_data[i]
            for i in indices
            if isinstance(i, int) and 0 <= i < len(faculty_data)
        ]
        if selected:
            logger.info(
                f"LLM filtered {len(faculty_data)} faculty down to {len(selected)}"
            )
            return selected
    except Exception:
        pass

    logger.warning("LLM filtering failed, falling back to first 30")
    return faculty_data[:30]


async def enrich_single_professor(
    faculty: dict[str, Any],
    university: str,
    domain_keywords: list[str],
) -> ProfessorProfile | None:
    """Enrich a single professor profile with publication data."""
    name = faculty.get("name", "")
    if not name:
        return None

    cached = await get_cached_professor(name, university)
    if cached:
        return cached

    # Check API cache for scholar search
    cached_scholars = await get_cached("scholar_search", name)
    if cached_scholars and isinstance(cached_scholars, list):
        candidates = cached_scholars
    else:
        candidates = await tools.search_scholar(name)
        if candidates:
            await set_cached(
                "scholar_search", name, data=candidates, ttl_days=30
            )

    # Filter by domain
    scholar = None
    if candidates:
        for cand in candidates:
            affiliations = cand.get("affiliations", [])
            if not affiliations:
                continue
            for aff in affiliations:
                aff_lower = aff.lower()
                if any(k in aff_lower for k in domain_keywords):
                    scholar = cand
                    break
            if scholar:
                break

        if not scholar:
            scholar = candidates[0]

    # If no scholar found, try to enrich from profile page
    if not scholar:
        profile_url = faculty.get("profile_url")
        extra_data: dict = {}
        if profile_url:
            try:
                page_content = await tools.fetch_page_content(profile_url)
                details = await gemini.extract_professor_details(page_content)
                extra_data = details.model_dump()
            except Exception:
                pass

        research_areas = extra_data.get("research_areas") or []
        if isinstance(research_areas, str):
            research_areas = [
                r.strip() for r in research_areas.split(",") if r.strip()
            ]

        prof = ProfessorProfile(
            id=uuid4(),
            name=name,
            title=faculty.get("title") or extra_data.get("title"),
            department=faculty.get("department") or extra_data.get("department"),
            university=university,
            email=faculty.get("email") or extra_data.get("email"),
            research_areas=research_areas,
            publications=[],
            last_updated=datetime.utcnow(),
        )
        await cache_professor(prof)
        return prof

    scholar_id = scholar.get("author_id")

    pubs_data = []
    if scholar_id:
        pubs_data = await tools.get_publications(scholar_id)

    publications = [
        Publication(
            title=p.get("title", ""),
            authors=p.get("authors", []),
            year=p.get("year", 0),
            venue=p.get("venue"),
            abstract=p.get("abstract"),
            citation_count=p.get("citation_count", 0),
            url=p.get("url"),
        )
        for p in pubs_data
    ]

    citation_metrics = CitationMetrics(
        h_index=0,
        total_citations=0,
    )

    research_areas = await _extract_research_areas(publications)

    prof = ProfessorProfile(
        id=uuid4(),
        name=name,
        title=faculty.get("title"),
        department=faculty.get("department"),
        university=university,
        email=faculty.get("email"),
        scholar_id=scholar_id,
        research_areas=research_areas,
        publications=publications,
        citation_metrics=citation_metrics,
        last_updated=datetime.utcnow(),
    )

    await cache_professor(prof)
    return prof


async def enrich_professors(
    faculty_data: list[dict[str, Any]], university: str
) -> list[ProfessorProfile]:
    """Enrich professor profiles with publication data (concurrent)."""
    domain = _extract_domain(university)
    parts = domain.lower().split(".")
    ignore = {
        "www",
        "ac",
        "za",
        "edu",
        "uk",
        "us",
        "com",
        "org",
        "net",
        "depts",
        "dept",
    }
    domain_keywords = [p for p in parts if p not in ignore and len(p) > 2]

    semaphore = asyncio.Semaphore(20)

    async def enrich_with_limit(
        faculty: dict[str, Any],
    ) -> ProfessorProfile | None:
        async with semaphore:
            return await enrich_single_professor(
                faculty, university, domain_keywords
            )

    tasks = [enrich_with_limit(f) for f in faculty_data]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    professors = []
    for result in results:
        if isinstance(result, ProfessorProfile):
            professors.append(result)

    return professors


async def _extract_research_areas(
    publications: list[Publication],
) -> list[str]:
    """Extract research areas from publication titles using LLM."""
    if not publications:
        return []

    titles = [p.title for p in publications[:15]]
    titles_str = "\n".join(f"- {t}" for t in titles)

    try:
        result = await gemini.extract_research_areas(titles_str)
        return result.areas[:7]
    except Exception:
        return []


async def enrich_matches_with_google_scholar(
    matches: list[MatchResult], university: str
) -> None:
    """Find Google Scholar URLs and scrape metrics for final matched professors."""
    domain = _extract_domain(university)

    async def enrich_one_match(match: MatchResult):
        try:
            if not match.professor.google_scholar_url:
                # Check cache for Google Scholar URL
                cached_url = await get_cached(
                    "gs_url", match.professor.name, domain
                )
                if cached_url and isinstance(cached_url, dict):
                    url = cached_url.get("url")
                else:
                    url = await tools.find_google_scholar_url(
                        match.professor.name, domain
                    )
                    await set_cached(
                        "gs_url",
                        match.professor.name,
                        domain,
                        data={"url": url},
                        ttl_days=30,
                    )
                if url:
                    match.professor.google_scholar_url = url

            if match.professor.google_scholar_url:
                # Check cache for metrics
                cached_metrics = await get_cached(
                    "gs_metrics", match.professor.google_scholar_url
                )
                if cached_metrics and isinstance(cached_metrics, dict) and not cached_metrics.get("error"):
                    metrics = cached_metrics
                else:
                    metrics = await tools.scrape_google_scholar_metrics(
                        match.professor.google_scholar_url
                    )
                    if metrics and not metrics.get("error"):
                        await set_cached(
                            "gs_metrics",
                            match.professor.google_scholar_url,
                            data=metrics,
                            ttl_days=7,
                        )

                if metrics and not metrics.get("error"):
                    match.professor.citation_metrics = CitationMetrics(
                        h_index=metrics.get("h_index", 0),
                        total_citations=metrics.get("total_citations", 0),
                    )
        except Exception:
            pass

    await asyncio.gather(
        *[enrich_one_match(m) for m in matches], return_exceptions=True
    )


async def generate_matches(
    professors: list[ProfessorProfile],
    research_interests: list[str],
    student_profile: StudentProfile | None,
) -> list[MatchResult]:
    """Generate ranked matches using LLM analysis."""
    if not professors:
        return []

    interests_str = ", ".join(research_interests)
    student_context = ""
    if student_profile:
        student_context = (
            "\nStudent Background:\n"
            f"- Education: {json.dumps([e.model_dump() for e in student_profile.education], default=str)}\n"
            f"- Skills: {', '.join(student_profile.skills)}\n"
            f"- Publications: {len(student_profile.publications)} papers\n"
            f"- Keywords: {', '.join(student_profile.extracted_keywords[:10])}"
        )

    prof_summaries = []
    for p in professors:
        prof_summaries.append(
            {
                "id": str(p.id),
                "name": p.name,
                "title": p.title,
                "department": p.department,
                "research_areas": p.research_areas[:5],
                "recent_papers": [pub.title for pub in p.publications[:5]],
                "h_index": p.citation_metrics.h_index
                if p.citation_metrics
                else 0,
            }
        )

    professors_json = json.dumps(prof_summaries, indent=2)

    try:
        result = await gemini.generate_match_rankings(
            professors_json, interests_str, student_context
        )

        matches = []
        prof_map = {str(p.id): p for p in professors}

        for m in result.matches[:10]:
            prof = prof_map.get(m.professor_id)
            if not prof:
                continue

            relevant_pubs = [
                p
                for p in prof.publications
                if p.title in m.relevant_publication_titles
            ]

            matches.append(
                MatchResult(
                    professor=prof,
                    match_score=m.match_score,
                    alignment_reasons=m.alignment_reasons,
                    relevant_publications=relevant_pubs,
                    shared_keywords=m.shared_keywords,
                    recommendation_text=m.recommendation_text,
                )
            )

        return sorted(matches, key=lambda x: x.match_score, reverse=True)
    except Exception:
        return []
