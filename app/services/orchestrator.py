import asyncio
import json
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
from app.services.gemini import generate_text
from app.services.mcp_client import (
    university_client,
    scholar_client,
    document_client,
    search_client,
)
from app.services.redis import get_session, set_session
from app.utils.storage import get_file_path


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
    await update_progress(session_id, 5, "Parsing uploaded documents")

    student_profile = None
    if file_ids:
        student_profile = await parse_student_documents(
            session_id, file_ids, research_interests
        )

    await update_progress(session_id, 15, "Fetching faculty directory")

    faculty_data = await fetch_faculty(university, research_interests)

    await update_progress(session_id, 25, "Filtering candidates")

    faculty_data = await filter_faculty_by_relevance(faculty_data, research_interests)

    await update_progress(session_id, 30, "Retrieving publication data")

    professors = await enrich_professors(faculty_data, university)

    await update_progress(session_id, 70, "Analyzing research alignment")

    matches = await generate_matches(professors, research_interests, student_profile)

    await update_progress(session_id, 90, "Fetching citation metrics")

    await enrich_matches_with_google_scholar(matches, university)

    await update_progress(session_id, 95, "Finalizing recommendations")

    session = await get_session(session_id)
    if session:
        # Calculate total matching time
        import time
        start_time = session.get("match_start_time")
        if start_time:
            total_time = time.time() - start_time
            session["total_match_time"] = total_time

        session["match_status"] = "completed"
        session["match_progress"] = 100
        session["current_step"] = "Complete"
        session["match_results"] = [m.model_dump(mode="json") for m in matches]
        await set_session(session_id, session)

    return matches


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

    # Parse all documents in parallel
    async def parse_single_file(file_id: str) -> dict | None:
        file_path = await get_file_path(session_id, file_id)
        if not file_path:
            return None
        return await document_client.parse_cv(str(file_path))

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
    from urllib.parse import urlparse

    try:
        domain = urlparse(url).netloc
        return domain.replace("www.", "").split("/")[0]
    except:
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

    # If it's a root domain OR a generic department page (e.g. /coe/), we Google.
    # Use the base domain (e.g. ttu.edu instead of www.ttu.edu) to catch subdomains (depts.ttu.edu)
    domain = parsed.netloc.replace("www.", "")
    query = f"Computer Science faculty directory {domain}"
    if interest:
        query = f"{interest} faculty directory {domain}"

    urls = await search_client.search_web(query)
    if urls:
        return urls  # Return ALL discovered URLs

    return [university]


async def fetch_faculty(
    university: str, research_interests: list[str]
) -> list[dict[str, Any]]:
    """Fetch faculty from university directory via MCP."""
    import logging

    logger = logging.getLogger(__name__)

    discovery_tasks = [
        discover_faculty_url(university, interest)
        for interest in research_interests[:3]
    ]
    discovery_results = await asyncio.gather(*discovery_tasks, return_exceptions=True)

    # Build (url, interest) pairs, deduplicating URLs
    seen_urls: set[str] = set()
    search_pairs: list[tuple[str, str]] = []
    for interest, result in zip(research_interests[:3], discovery_results):
        if isinstance(result, Exception):
            logger.warning(f"Faculty URL discovery failed for '{interest}': {result}")
            continue
        for url in result:
            if url not in seen_urls:
                seen_urls.add(url)
                search_pairs.append((url, interest))

    async def search_one(url: str, interest: str) -> list[dict]:
        logger.info(f"Searching faculty for interest: {interest} at {url}")
        result = await university_client.search_faculty(url, interest)
        if isinstance(result, dict):
            logger.warning(
                f"Faculty search error at {url}: {result.get('error') or result.get('raw', 'Unknown error')}"
            )
            return []
        if not isinstance(result, list):
            logger.warning(f"Unexpected result type at {url}: {type(result)}")
            return []
        faculty_dicts = [f for f in result if isinstance(f, dict) and f.get("name")]
        logger.info(f"Found {len(faculty_dicts)} faculty members at {url}")
        return faculty_dicts

    fetch_tasks = [search_one(url, interest) for url, interest in search_pairs]
    fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    all_faculty = []
    for result in fetch_results:
        if isinstance(result, list):
            all_faculty.extend(result)

    seen_names = set()
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
    import logging

    logger = logging.getLogger(__name__)

    if len(faculty_data) <= 30:
        return faculty_data

    interests_str = ", ".join(research_interests)

    # Build lightweight summaries (name + title + department only)
    summaries = []
    for i, f in enumerate(faculty_data):
        parts = [f"[{i}] {f.get('name', 'Unknown')}"]
        if f.get("title"):
            parts.append(f"- {f['title']}")
        if f.get("department"):
            parts.append(f"({f['department']})")
        summaries.append(" ".join(parts))

    faculty_list = "\n".join(summaries)

    prompt = f"""From this faculty list, select the 30 professors most likely to research: {interests_str}

Faculty:
{faculty_list}

Return ONLY a JSON array of the index numbers (e.g. [0, 3, 7, ...]). No other text."""

    response = await generate_text(prompt)

    try:
        json_match = __import__("re").search(r"\[.*\]", response, __import__("re").DOTALL)
        if json_match:
            indices = json.loads(json_match.group())
            selected = [faculty_data[i] for i in indices if isinstance(i, int) and 0 <= i < len(faculty_data)]
            if selected:
                logger.info(f"LLM filtered {len(faculty_data)} faculty down to {len(selected)}")
                return selected
    except (json.JSONDecodeError, IndexError, TypeError):
        pass

    # Fallback: return first 30
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

    # Search Semantic Scholar only (Google Scholar URL search deferred to final matches)
    candidates = await scholar_client.search_scholar(name)

    # Filter by Domain
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
        extra_data = {}
        if profile_url:
            try:
                extra_data = await university_client.get_professor_page(profile_url)
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
            last_updated=datetime.utcnow(),
        )
        await cache_professor(prof)
        return prof

    scholar_id = scholar.get("author_id")

    # Fetch only publications (skip metrics - we'll scrape from Google Scholar later)
    pubs_data = []
    if scholar_id:
        pubs_data = await scholar_client.get_publications(scholar_id)

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

    # Placeholder metrics - will be scraped from Google Scholar for final matches
    citation_metrics = CitationMetrics(
        h_index=0,
        total_citations=0,
    )

    research_areas = await extract_research_areas(publications)

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
    """Enrich professor profiles with publication data via MCP (concurrent)."""
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

    # Higher concurrency for faster processing (increased since we skip metrics call)
    semaphore = asyncio.Semaphore(20)

    async def enrich_with_limit(faculty: dict[str, Any]) -> ProfessorProfile | None:
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


async def extract_research_areas(publications: list[Publication]) -> list[str]:
    """Extract research areas from publication titles using LLM."""
    if not publications:
        return []

    titles = [p.title for p in publications[:15]]
    titles_str = "\n".join(f"- {t}" for t in titles)

    prompt = f"""From these publication titles, extract 3-7 research areas/topics.
Return short, specific phrases (e.g. "computer vision", "natural language processing", "reinforcement learning").

Publications:
{titles_str}

Return ONLY a JSON array of strings. No other text."""

    response = await generate_text(prompt)

    try:
        json_match = __import__("re").search(r"\[.*\]", response, __import__("re").DOTALL)
        if json_match:
            areas = json.loads(json_match.group())
            if areas and isinstance(areas, list):
                return [str(a) for a in areas[:7]]
    except (json.JSONDecodeError, TypeError):
        pass

    return []


async def enrich_matches_with_google_scholar(matches: list[MatchResult], university: str) -> None:
    """Find Google Scholar URLs and scrape metrics for final matched professors."""
    domain = _extract_domain(university)

    async def enrich_one_match(match: MatchResult):
        """Find Google Scholar URL and scrape metrics for a single match."""
        try:
            # Find Google Scholar URL if not already set
            if not match.professor.google_scholar_url:
                url = await search_client.find_google_scholar_url(
                    match.professor.name, domain
                )
                if url:
                    match.professor.google_scholar_url = url

            # Scrape metrics if we have a URL
            if match.professor.google_scholar_url:
                metrics = await scholar_client.scrape_google_scholar_metrics(
                    match.professor.google_scholar_url
                )
                if metrics and not metrics.get("error"):
                    match.professor.citation_metrics = CitationMetrics(
                        h_index=metrics.get("h_index", 0),
                        total_citations=metrics.get("total_citations", 0),
                    )
        except Exception:
            pass

    await asyncio.gather(*[enrich_one_match(m) for m in matches], return_exceptions=True)


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
        student_context = f"""
Student Background:
- Education: {json.dumps([e.model_dump() for e in student_profile.education], default=str)}
- Skills: {", ".join(student_profile.skills)}
- Publications: {len(student_profile.publications)} papers
- Keywords: {", ".join(student_profile.extracted_keywords[:10])}
"""

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
                "h_index": p.citation_metrics.h_index if p.citation_metrics else 0,
            }
        )

    prompt = f"""Analyze professors and rank by research alignment with student interests.

Student Research Interests: {interests_str}
{student_context}

Professors:
{json.dumps(prof_summaries, indent=2)}

Return JSON array (top 10 max) with:
- professor_id: string
- match_score: number (0-100)
- alignment_reasons: string[] (2-3 specific reasons why this professor is a good match)
- relevant_publication_titles: string[] (select publications that the student could cite or build upon for their research - papers most aligned with student's interests)
- shared_keywords: string[] (research topics/keywords shared between student interests and professor's work)
- recommendation_text: string (2-3 sentences explaining why this professor would be valuable for the student's research)

Return ONLY valid JSON array, no other text."""

    response = await generate_text(prompt)

    try:
        json_match = __import__("re").search(
            r"\[.*\]", response, __import__("re").DOTALL
        )
        if not json_match:
            return []

        matches_data = json.loads(json_match.group())
        matches = []
        prof_map = {str(p.id): p for p in professors}

        for m in matches_data[:10]:
            prof = prof_map.get(m.get("professor_id"))
            if not prof:
                continue

            relevant_pubs = [
                p
                for p in prof.publications
                if p.title in m.get("relevant_publication_titles", [])
            ]

            matches.append(
                MatchResult(
                    professor=prof,
                    match_score=float(m.get("match_score", 0)),
                    alignment_reasons=m.get("alignment_reasons", []),
                    relevant_publications=relevant_pubs,
                    shared_keywords=m.get("shared_keywords", []),
                    recommendation_text=m.get("recommendation_text", ""),
                )
            )

        sorted_matches = sorted(matches, key=lambda x: x.match_score, reverse=True)

        return sorted_matches
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
