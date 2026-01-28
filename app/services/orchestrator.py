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

    await update_progress(session_id, 35, "Retrieving publication data")

    professors = await enrich_professors(faculty_data, university)

    await update_progress(session_id, 65, "Analyzing research alignment")

    matches = await generate_matches(professors, research_interests, student_profile)

    await update_progress(session_id, 95, "Finalizing recommendations")

    session = await get_session(session_id)
    if session:
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

    return unique_faculty[:30]


async def enrich_single_professor(
    faculty: dict[str, Any],
    university: str,
    domain: str,
    domain_keywords: list[str],
) -> ProfessorProfile | None:
    """Enrich a single professor profile with publication data."""
    name = faculty.get("name", "")
    if not name:
        return None

    cached = await get_cached_professor(name, university)
    if cached:
        return cached

    # Run Semantic Scholar search and Google Scholar URL search concurrently
    scholar_task = scholar_client.search_scholar(name)
    google_scholar_task = search_client.find_google_scholar_url(name, domain)

    candidates, google_scholar_url = await asyncio.gather(
        scholar_task, google_scholar_task
    )

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
            google_scholar_url=google_scholar_url,
            research_areas=research_areas,
            publications=[],
            last_updated=datetime.utcnow(),
        )
        await cache_professor(prof)
        return prof

    scholar_id = scholar.get("author_id")

    # Fetch publications and metrics concurrently
    tasks_to_run = []
    if scholar_id:
        tasks_to_run.append(scholar_client.get_publications(scholar_id))
        tasks_to_run.append(scholar_client.get_citation_metrics(scholar_id))

    if tasks_to_run:
        pubs_data, metrics_data = await asyncio.gather(*tasks_to_run)
    else:
        pubs_data, metrics_data = [], {}

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
        h_index=metrics_data.get("h_index", 0),
        total_citations=metrics_data.get("total_citations", 0),
    )

    research_areas = extract_research_areas(publications)

    prof = ProfessorProfile(
        id=uuid4(),
        name=name,
        title=faculty.get("title"),
        department=faculty.get("department"),
        university=university,
        email=faculty.get("email"),
        scholar_id=scholar_id,
        google_scholar_url=google_scholar_url,
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

    # Higher concurrency for faster processing
    semaphore = asyncio.Semaphore(10)

    async def enrich_with_limit(faculty: dict[str, Any]) -> ProfessorProfile | None:
        async with semaphore:
            return await enrich_single_professor(
                faculty, university, domain, domain_keywords
            )

    tasks = [enrich_with_limit(f) for f in faculty_data]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    professors = []
    for result in results:
        if isinstance(result, ProfessorProfile):
            professors.append(result)

    return professors


def extract_research_areas(publications: list[Publication]) -> list[str]:
    """Extract research areas from publication titles."""
    words: dict[str, int] = {}
    stopwords = {
        "the",
        "a",
        "an",
        "of",
        "in",
        "for",
        "and",
        "to",
        "on",
        "with",
        "using",
        "based",
        "via",
    }

    for pub in publications:
        for word in pub.title.lower().split():
            word = word.strip(".,;:()[]")
            if len(word) > 3 and word not in stopwords:
                words[word] = words.get(word, 0) + 1

    sorted_words = sorted(words.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in sorted_words[:10]]


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

        return sorted(matches, key=lambda x: x.match_score, reverse=True)
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
