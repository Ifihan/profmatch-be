import asyncio
import json
import logging
import time
from datetime import UTC, datetime
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
from app.services.cache import (
    cache_faculty,
    cache_professor,
    get_cached_faculty,
    get_cached_professor,
    get_cached_professor_by_openalex_id,
)
from app.services import gemini, openalex, tools
from app.services.session_store import get_session, set_session
from app.utils.storage import get_file_path

logger = logging.getLogger(__name__)


async def update_progress(*, session_id: str, progress: int, step: str) -> None:
    """Update matching progress in session."""
    session = await get_session(session_id=session_id)
    if session:
        session["match_progress"] = progress
        session["current_step"] = step
        await set_session(session_id=session_id, data=session)


async def run_matching(
    *,
    session_id: str,
    university: str,
    research_interests: list[str],
    file_ids: list[str] | None = None,
) -> list[MatchResult]:
    """Run the full matching pipeline."""
    wide_event: dict[str, Any] = {
        "event": "matching_pipeline",
        "session_id": session_id,
        "university": university,
        "research_interests": research_interests,
        "file_count": len(file_ids) if file_ids else 0,
        "start_time": time.time(),
    }

    try:
        await update_progress(
            session_id=session_id, progress=5, step="Parsing documents and discovering faculty"
        )

        # run CV parsing and faculty fetch concurrently (they're independent)
        async def _parse_cv_if_needed():
            if not file_ids:
                return None
            return await parse_student_documents(
                session_id=session_id,
                file_ids=file_ids,
                research_interests=research_interests,
            )

        student_profile, (faculty_data, faculty_warnings) = await asyncio.gather(
            _parse_cv_if_needed(),
            fetch_faculty(university=university, research_interests=research_interests),
        )
        wide_event["student_profile_parsed"] = student_profile is not None
        wide_event["faculty_found"] = len(faculty_data)
        wide_event["faculty_source"] = "openalex" if not faculty_warnings else "mixed"
        if faculty_warnings:
            wide_event["faculty_warnings"] = faculty_warnings

        await update_progress(
            session_id=session_id, progress=25, step="Filtering candidates"
        )

        faculty_data = filter_faculty_by_relevance(
            faculty_data=faculty_data, research_interests=research_interests
        )
        wide_event["faculty_filtered"] = len(faculty_data)

        await update_progress(
            session_id=session_id, progress=30, step="Retrieving publication data"
        )

        professors, enrichment_errors = await enrich_professors(
            faculty_data=faculty_data, university=university
        )
        wide_event["professors_enriched"] = len(professors)
        if enrichment_errors:
            wide_event["enrichment_errors"] = enrichment_errors

        await update_progress(
            session_id=session_id, progress=70, step="Analyzing research alignment"
        )

        matches = await generate_matches(
            professors=professors,
            research_interests=research_interests,
            student_profile=student_profile,
        )
        wide_event["matches_generated"] = len(matches)

        await update_progress(
            session_id=session_id, progress=95, step="Finalizing recommendations"
        )

        session = await get_session(session_id=session_id)
        if session:
            if start_time := session.get("match_start_time"):
                total_time = time.time() - start_time
                session["total_match_time"] = total_time
                wide_event["total_time_seconds"] = total_time

            session["match_status"] = "completed"
            session["match_progress"] = 100
            session["current_step"] = "Complete"
            session["match_results"] = [
                m.model_dump(mode="json") for m in matches
            ]
            await set_session(session_id=session_id, data=session)

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
    *,
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
        text = tools.extract_text_from_file(file_path=str(file_path))
        parsed = await gemini.parse_cv(text=text)
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


# ===================================================================
# Faculty Discovery (OpenAlex primary, web scraping fallback)
# ===================================================================


async def fetch_faculty(
    *, university: str, research_interests: list[str]
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Fetch faculty using OpenAlex, falling back to web scraping.

    Returns (faculty_list, warnings).
    """
    warnings: list[dict] = []

    try:
        faculty, openalex_warnings = await _fetch_faculty_openalex(
            university=university, research_interests=research_interests
        )
        warnings.extend(openalex_warnings)
        if faculty:
            return faculty, warnings
    except Exception as e:
        warnings.append({
            "stage": "openalex_discovery",
            "error": str(e),
        })

    # fallback to web scraping
    try:
        faculty, scraping_warnings = await _fetch_faculty_fallback(
            university=university, research_interests=research_interests
        )
        warnings.extend(scraping_warnings)
        return faculty, warnings
    except Exception as e:
        warnings.append({
            "stage": "scraping_fallback",
            "error": str(e),
        })
        return [], warnings


async def _fetch_faculty_openalex(
    *, university: str, research_interests: list[str]
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Discover faculty via OpenAlex institution + author search."""
    warnings: list[dict] = []

    institution = await openalex.resolve_institution(query=university)
    if not institution:
        warnings.append({
            "stage": "openalex_institution",
            "error": f"could not resolve institution: {university}",
        })
        return [], warnings

    authors = await openalex.get_authors_by_institution(
        institution_id=institution["id"],
        topics=research_interests,
        limit=50,
    )

    if not authors:
        warnings.append({
            "stage": "openalex_authors",
            "error": "no authors found for institution + topics",
        })
        return [], warnings

    faculty = [
        {
            "name": a["name"],
            "title": None,
            "department": None,
            "email": None,
            "profile_url": None,
            "openalex_id": a["openalex_id"],
            "topics": a["topics"],
            "topic_details": a["topic_details"],
            "h_index": a["h_index"],
            "i10_index": a["i10_index"],
            "cited_by_count": a["cited_by_count"],
            "works_count": a["works_count"],
            "orcid": a["orcid"],
        }
        for a in authors
    ]

    return faculty, warnings


async def _discover_faculty_url(*, university: str, interest: str) -> list[str]:
    """Discover specific faculty directory URL(s) via web search if generic."""
    if not university.startswith(("http://", "https://")):
        university = "https://" + university
    parsed = urlparse(university)

    path = parsed.path.lower()
    explicit_keywords = [
        "faculty", "staff", "people", "directory", "team", "professors",
    ]

    if any(k in path for k in explicit_keywords):
        return [university]

    domain = parsed.netloc.replace("www.", "")
    query = f"{interest} faculty directory {domain}" if interest else f"Computer Science faculty directory {domain}"

    urls = await tools.search_web(query=query)
    return urls if urls else [university]


async def _fetch_faculty_fallback(
    *, university: str, research_interests: list[str]
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Fallback: discover faculty via web scraping (Serper + trafilatura + Gemini)."""
    discovery_tasks = [
        _discover_faculty_url(university=university, interest=interest)
        for interest in research_interests[:3]
    ]
    discovery_results = await asyncio.gather(
        *discovery_tasks, return_exceptions=True
    )

    seen_urls: set[str] = set()
    search_pairs: list[tuple[str, str]] = []
    warnings: list[dict] = []
    for interest, result in zip(research_interests[:3], discovery_results):
        if isinstance(result, Exception):
            warnings.append({
                "stage": "url_discovery",
                "interest": interest,
                "error": str(result),
            })
            continue
        for url in result:
            if url not in seen_urls:
                seen_urls.add(url)
                search_pairs.append((url, interest))

    async def search_one(url: str, interest: str) -> list[dict]:
        cached = await get_cached_faculty(source_url=url)
        if cached is not None:
            faculty_dicts = cached
        else:
            faculty_keywords = [
                "faculty", "staff", "people", "directory", "team", "professors",
            ]
            path_lower = url.lower().split("?")[0]
            faculty_url = url

            if not any(k in path_lower for k in faculty_keywords):
                try:
                    page_content = await tools.fetch_page_content(url=url)
                    found_url = await gemini.find_faculty_directory_url(
                        page_content=page_content, base_url=url
                    )
                    if found_url:
                        faculty_url = found_url
                    else:
                        warnings.append({
                            "stage": "directory_discovery",
                            "url": url,
                            "error": "no faculty directory found",
                        })
                        return []
                except Exception as e:
                    warnings.append({
                        "stage": "directory_discovery",
                        "url": url,
                        "error": str(e),
                    })
                    return []

            try:
                page_content = await tools.fetch_page_content(url=faculty_url)
                members = await gemini.extract_faculty(
                    page_content=page_content, url=faculty_url
                )
                faculty_dicts = [m.model_dump() for m in members]
                await cache_faculty(
                    source_url=url,
                    university=university,
                    members=faculty_dicts,
                )
            except Exception as e:
                warnings.append({
                    "stage": "faculty_extraction",
                    "url": url,
                    "error": str(e),
                })
                return []

        stopwords = {
            "in", "the", "of", "and", "for", "a", "an",
            "to", "on", "with", "field", "area", "using", "based",
        }
        keywords = [
            w.lower().strip(".,;:()")
            for w in interest.split()
            if w.lower().strip(".,;:()") not in stopwords
            and len(w.strip(".,;:()")) > 2
        ]

        scored = []
        for f in faculty_dicts:
            text = " ".join([
                (f.get("name") or ""),
                (f.get("title") or ""),
                (f.get("department") or ""),
            ]).lower()
            score = sum(1 for kw in keywords if kw in text)
            scored.append((f, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [f for f, _ in scored if isinstance(f, dict) and f.get("name")]

    fetch_tasks = [search_one(url, interest) for url, interest in search_pairs]
    fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    all_faculty: list[dict] = []
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

    return unique_faculty, warnings


# ===================================================================
# Faculty Filtering (algorithmic topic-overlap scoring)
# ===================================================================


def filter_faculty_by_relevance(
    *, faculty_data: list[dict[str, Any]], research_interests: list[str]
) -> list[dict[str, Any]]:
    """Filter faculty by topic overlap with research interests.

    Uses OpenAlex topic hierarchy when available (domain > field > subfield > topic).
    Falls back to keyword matching on name/title/department for scraped faculty.
    Returns top 30 by score.
    """
    if len(faculty_data) <= 30:
        return faculty_data

    interest_tokens = set()
    for interest in research_interests:
        for word in interest.lower().split():
            cleaned = word.strip(".,;:()")
            if len(cleaned) > 2:
                interest_tokens.add(cleaned)

    # also keep full interest phrases for exact matching
    interest_phrases = [i.lower() for i in research_interests]

    scored: list[tuple[dict, float]] = []
    for f in faculty_data:
        score = _compute_topic_score(
            faculty=f,
            interest_tokens=interest_tokens,
            interest_phrases=interest_phrases,
        )
        scored.append((f, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [f for f, _ in scored[:30]]


def _compute_topic_score(
    *,
    faculty: dict,
    interest_tokens: set[str],
    interest_phrases: list[str],
) -> float:
    """Compute relevance score for a faculty member against research interests.

    Weighting: exact topic match (3.0) > subfield match (2.0) > field match (1.0) > domain match (0.5)
    """
    score = 0.0

    # OpenAlex topic details (from primary path)
    topic_details = faculty.get("topic_details", [])
    if topic_details:
        for td in topic_details:
            topic_name = (td.get("name") or "").lower()
            subfield = (td.get("subfield") or "").lower()
            field = (td.get("field") or "").lower()
            domain = (td.get("domain") or "").lower()

            for phrase in interest_phrases:
                if phrase in topic_name or topic_name in phrase:
                    score += 3.0
                elif phrase in subfield or subfield in phrase:
                    score += 2.0
                elif phrase in field or field in phrase:
                    score += 1.0
                elif phrase in domain or domain in phrase:
                    score += 0.5

            for token in interest_tokens:
                if token in topic_name:
                    score += 1.0
                elif token in subfield:
                    score += 0.5
        return score

    # plain topic names (from primary path, simpler)
    topics = faculty.get("topics", [])
    if topics:
        for t in topics:
            t_lower = t.lower()
            for phrase in interest_phrases:
                if phrase in t_lower or t_lower in phrase:
                    score += 3.0
            for token in interest_tokens:
                if token in t_lower:
                    score += 1.0
        return score

    # fallback for scraped faculty (no topic data): keyword match on metadata
    text = " ".join([
        (faculty.get("name") or ""),
        (faculty.get("title") or ""),
        (faculty.get("department") or ""),
    ]).lower()
    for token in interest_tokens:
        if token in text:
            score += 1.0

    return score


# ===================================================================
# Professor Enrichment (OpenAlex primary, scraping fallback)
# ===================================================================


async def enrich_single_professor(
    *, faculty: dict[str, Any], university: str
) -> ProfessorProfile | None:
    """Enrich a single professor profile with publication and citation data."""
    name = faculty.get("name", "")
    if not name:
        return None

    openalex_id = faculty.get("openalex_id")

    # check cache by openalex_id first, then by name+university
    if openalex_id:
        if cached := await get_cached_professor_by_openalex_id(openalex_id=openalex_id):
            return cached
    if cached := await get_cached_professor(name=name, university=university):
        return cached

    # OpenAlex primary path: author already has metrics from discovery
    if openalex_id:
        return await _enrich_from_openalex(faculty=faculty, university=university)

    # fallback: scrape professor profile page
    return await _enrich_from_scraping(faculty=faculty, university=university)


async def _enrich_from_openalex(
    *, faculty: dict[str, Any], university: str
) -> ProfessorProfile:
    """Enrich professor using OpenAlex works API."""
    openalex_id = faculty["openalex_id"]

    works = await openalex.get_author_works(
        author_id=openalex_id, limit=20, years=5
    )

    publications = [
        Publication(
            title=w.get("title", ""),
            authors=w.get("authors", []),
            year=w.get("publication_year", 0),
            venue=w.get("venue"),
            abstract=w.get("abstract"),
            citation_count=w.get("citation_count", 0),
            url=w.get("doi"),
        )
        for w in works
    ]

    # research areas come from OpenAlex topics (already in the author record)
    research_areas = faculty.get("topics", [])[:7]

    citation_metrics = CitationMetrics(
        h_index=faculty.get("h_index", 0),
        i10_index=faculty.get("i10_index", 0),
        total_citations=faculty.get("cited_by_count", 0),
    )

    prof = ProfessorProfile(
        id=uuid4(),
        name=faculty["name"],
        title=faculty.get("title"),
        department=faculty.get("department"),
        university=university,
        email=faculty.get("email"),
        openalex_id=openalex_id,
        research_areas=research_areas,
        publications=publications,
        citation_metrics=citation_metrics,
        last_updated=datetime.now(UTC),
    )

    await cache_professor(profile=prof)
    return prof


async def _enrich_from_scraping(
    *, faculty: dict[str, Any], university: str
) -> ProfessorProfile:
    """Fallback enrichment: scrape professor profile page with Gemini."""
    name = faculty["name"]
    profile_url = faculty.get("profile_url")
    extra_data: dict = {}

    if profile_url:
        try:
            page_content = await tools.fetch_page_content(url=profile_url)
            details = await gemini.extract_professor_details(
                page_content=page_content
            )
            extra_data = details.model_dump()
        except Exception:
            pass

    research_areas = extra_data.get("research_areas") or []
    if isinstance(research_areas, str):
        research_areas = [r.strip() for r in research_areas.split(",") if r.strip()]

    prof = ProfessorProfile(
        id=uuid4(),
        name=name,
        title=faculty.get("title") or extra_data.get("title"),
        department=faculty.get("department") or extra_data.get("department"),
        university=university,
        email=faculty.get("email") or extra_data.get("email"),
        research_areas=research_areas,
        publications=[],
        last_updated=datetime.now(UTC),
    )
    await cache_professor(profile=prof)
    return prof


async def enrich_professors(
    *, faculty_data: list[dict[str, Any]], university: str
) -> tuple[list[ProfessorProfile], list[dict]]:
    """Enrich professor profiles with publication data (concurrent).

    Returns (professors, errors).
    """
    semaphore = asyncio.Semaphore(20)

    async def enrich_with_limit(faculty: dict[str, Any]) -> ProfessorProfile | None:
        async with semaphore:
            return await enrich_single_professor(
                faculty=faculty, university=university
            )

    tasks = [enrich_with_limit(f) for f in faculty_data]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    professors = []
    errors = []
    for faculty, result in zip(faculty_data, results):
        if isinstance(result, ProfessorProfile):
            professors.append(result)
        elif isinstance(result, Exception):
            errors.append({
                "name": faculty.get("name", "unknown"),
                "error_type": type(result).__name__,
                "error_message": str(result),
            })

    return professors, errors


# ===================================================================
# Match Generation
# ===================================================================


async def generate_matches(
    *,
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
        prof_summaries.append({
            "id": str(p.id),
            "name": p.name,
            "title": p.title,
            "department": p.department,
            "research_areas": p.research_areas[:5],
            "recent_papers": [pub.title for pub in p.publications[:5]],
            "h_index": p.citation_metrics.h_index if p.citation_metrics else 0,
        })

    professors_json = json.dumps(prof_summaries, indent=2)

    try:
        result = await gemini.generate_match_rankings(
            professors_json=professors_json,
            interests=interests_str,
            student_context=student_context,
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
