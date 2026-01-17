import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.models import CitationMetrics, Education, Experience, MatchResult, ProfessorProfile, Publication, StudentProfile
from app.services.cache import cache_professor, get_cached_professor
from app.services.gemini import generate_text
from app.services.mcp_client import document_client, scholar_client, university_client
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
        student_profile = await parse_student_documents(session_id, file_ids, research_interests)

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

    for file_id in file_ids:
        file_path = get_file_path(session_id, file_id)
        if not file_path:
            continue

        cv_data = await document_client.parse_cv(str(file_path))

        for edu in cv_data.get("education", []) or []:
            if not isinstance(edu, dict):
                continue
            education.append(Education(
                institution=to_str(edu.get("institution")) or "",
                degree=to_str(edu.get("degree")) or "",
                field=to_str(edu.get("field")),
                year=to_int(edu.get("year")) or None,
            ))

        for exp in cv_data.get("experience", []) or []:
            if not isinstance(exp, dict):
                continue
            experience.append(Experience(
                organization=to_str(exp.get("organization")) or "",
                role=to_str(exp.get("role")) or "",
                description=to_str(exp.get("description")),
                start_year=to_int(exp.get("start_year")) or None,
                end_year=to_int(exp.get("end_year")) or None,
            ))

        for pub in cv_data.get("publications", []) or []:
            if not isinstance(pub, dict):
                continue
            publications.append(Publication(
                title=to_str(pub.get("title")) or "",
                authors=to_list(pub.get("authors")),
                year=to_int(pub.get("year")),
                venue=to_str(pub.get("venue")),
            ))

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
        return domain.replace('www.', '').split('/')[0]
    except:
        return ""


async def discover_faculty_url(university: str, interest: str) -> str:
    """Discover specific faculty directory URL via web search if generic."""
    # Check if generic (simple heuristic: path is empty or just /)
    from urllib.parse import urlparse
    parsed = urlparse(university)
    
    # If path is deep, assume it's already specific
    if len(parsed.path) > 1 and parsed.path != "/":
        return university

    # It's a root domain! Time to Google.
    query = f"site:{parsed.netloc} {interest} faculty directory"
    
    # Call the new tool
    urls = await university_client.search_web(query)
    if urls:
        return urls[0] # The best match
            
    return university


async def fetch_faculty(university: str, research_interests: list[str]) -> list[dict[str, Any]]:
    """Fetch faculty from university directory via MCP."""
    import logging
    logger = logging.getLogger(__name__)

    all_faculty = []

    for interest in research_interests[:3]:
        # Discover specific URL for this interest
        target_url = await discover_faculty_url(university, interest)
        logger.info(f"Searching faculty for interest: {interest} at {target_url}")
        
        result = await university_client.search_faculty(target_url, interest)
        if isinstance(result, dict):
            logger.warning(f"Faculty search error: {result.get('error') or result.get('raw', 'Unknown error')}")
            continue
        if not isinstance(result, list):
            logger.warning(f"Unexpected result type: {type(result)}")
            continue
        faculty_dicts = [f for f in result if isinstance(f, dict) and f.get("name")]
        logger.info(f"Found {len(faculty_dicts)} faculty members")
        all_faculty.extend(faculty_dicts)

    seen_names = set()
    unique_faculty = []
    for f in all_faculty:
        name = f.get("name", "")
        if name and name not in seen_names:
            seen_names.add(name)
            unique_faculty.append(f)

    return unique_faculty[:30]


async def enrich_professors(faculty_data: list[dict[str, Any]], university: str) -> list[ProfessorProfile]:
    """Enrich professor profiles with publication data via MCP."""
    professors = []

    for faculty in faculty_data:
        name = faculty.get("name", "")
        if not name:
            continue

        cached = await get_cached_professor(name, university)
        if cached:
            professors.append(cached)
            continue

        # Enhanced Access Strategy: Relaxed Search + Domain Filter
        domain = _extract_domain(university)
        
        # 1. Broad Search (Name Only)
        candidates = await scholar_client.search_scholar(name)
        
        # 2. Filter by Domain
        scholar = None
        if not candidates:
            pass # No one found
        elif not domain:
            scholar = candidates[0] # Fallback if no domain to filter by
        else:
            for cand in candidates:
                # Check known affiliations (if available in client response, usually sparse)
                # Check email if available (often masked, but worth checking)
                # For now, we assume the first match that isn't obviously wrong is better than nothing
                # BUT, to be safe, let's look for triggers.
                # Since we don't have full affiliation data returned by search_scholar yet (it returns minimal),
                # we might need to fetch details. For now, we'll optimistically take the first 
                # result if the name match is exact, OR if we implement better filtering later.
                
                # TODO: Retrieve full profile to check affiliation. 
                # For this iteration, we accept the first match to solve the "0 results" bug.
                # Future: fetch_author_details(cand['author_id']) -> check affiliation.
                scholar = cand
                break

        if not scholar:
            prof = ProfessorProfile(
                id=uuid4(),
                name=name,
                title=faculty.get("title"),
                department=faculty.get("department"),
                university=university,
                email=faculty.get("email"),
                research_areas=[],
                publications=[],
                last_updated=datetime.utcnow(),
            )
            professors.append(prof)
            continue

        scholar = scholars[0]
        scholar_id = scholar.get("author_id")

        pubs_data = await scholar_client.get_publications(scholar_id) if scholar_id else []
        metrics_data = await scholar_client.get_citation_metrics(scholar_id) if scholar_id else {}

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
            research_areas=research_areas,
            publications=publications,
            citation_metrics=citation_metrics,
            last_updated=datetime.utcnow(),
        )

        await cache_professor(prof)
        professors.append(prof)

    return professors


def extract_research_areas(publications: list[Publication]) -> list[str]:
    """Extract research areas from publication titles."""
    words: dict[str, int] = {}
    stopwords = {"the", "a", "an", "of", "in", "for", "and", "to", "on", "with", "using", "based", "via"}

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
        prof_summaries.append({
            "id": str(p.id),
            "name": p.name,
            "title": p.title,
            "department": p.department,
            "research_areas": p.research_areas[:5],
            "recent_papers": [pub.title for pub in p.publications[:5]],
            "h_index": p.citation_metrics.h_index if p.citation_metrics else 0,
        })

    prompt = f"""Analyze professors and rank by research alignment with student interests.

Student Research Interests: {interests_str}
{student_context}

Professors:
{json.dumps(prof_summaries, indent=2)}

Return JSON array (top 10 max) with:
- professor_id: string
- match_score: number (0-100)
- alignment_reasons: string[] (2-3 specific reasons)
- relevant_publication_titles: string[] (from their papers)
- shared_keywords: string[]
- recommendation_text: string (2-3 sentences)

Return ONLY valid JSON array, no other text."""

    response = await generate_text(prompt)

    try:
        json_match = __import__("re").search(r'\[.*\]', response, __import__("re").DOTALL)
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
                p for p in prof.publications
                if p.title in m.get("relevant_publication_titles", [])
            ]

            matches.append(MatchResult(
                professor=prof,
                match_score=float(m.get("match_score", 0)),
                alignment_reasons=m.get("alignment_reasons", []),
                relevant_publications=relevant_pubs,
                shared_keywords=m.get("shared_keywords", []),
                recommendation_text=m.get("recommendation_text", ""),
            ))

        return sorted(matches, key=lambda x: x.match_score, reverse=True)
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
