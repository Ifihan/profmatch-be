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
    get_cached_professors_batch,
)
from app.services import gemini, openalex, tools
from app.services.session_store import get_session, set_session, update_session_fields
from app.utils.storage import get_file_path

logger = logging.getLogger(__name__)


async def update_progress(*, session_id: str, progress: int, step: str) -> None:
    """Update matching progress in session (single DB round trip)."""
    await update_session_fields(
        session_id=session_id,
        updates={"match_progress": progress, "current_step": step},
    )


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

        student_profile, (faculty_data, faculty_warnings, institution_name) = await asyncio.gather(
            _parse_cv_if_needed(),
            fetch_faculty(university=university, research_interests=research_interests),
        )
        wide_event["student_profile_parsed"] = student_profile is not None
        wide_event["faculty_found"] = len(faculty_data)
        wide_event["institution_name"] = institution_name
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

        # Use institution display name for professor profiles
        display_university = institution_name or university

        professors, enrichment_errors = await enrich_professors(
            faculty_data=faculty_data,
            university=display_university,
            university_url=university,
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
            session_id=session_id, progress=85, step="Fetching supplementary data"
        )

        # Post-match: fetch supplementary data (email, Google Scholar, website)
        # only for the selected top matches — saves ~80% of Serper API calls
        await post_enrich_matches(matches=matches, university_url=university)

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

    # Check session for pre-parsed CVs (parsed during upload)
    session_data = await get_session(session_id=session_id) or {}
    pre_parsed = session_data.get("parsed_cvs", {})

    async def parse_single_file(file_id: str) -> dict | None:
        # Use pre-parsed data if available (parsed during upload background task)
        if file_id in pre_parsed:
            return pre_parsed[file_id]
        # Fallback: download from GCS and parse
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
) -> tuple[list[dict[str, Any]], list[dict], str | None]:
    """Fetch faculty using OpenAlex, falling back to web scraping.

    Returns (faculty_list, warnings, institution_display_name).
    """
    warnings: list[dict] = []
    institution_name: str | None = None

    try:
        faculty, openalex_warnings, institution_name = await _fetch_faculty_openalex(
            university=university, research_interests=research_interests
        )
        warnings.extend(openalex_warnings)
        if faculty:
            return faculty, warnings, institution_name
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
        return faculty, warnings, institution_name
    except Exception as e:
        warnings.append({
            "stage": "scraping_fallback",
            "error": str(e),
        })
        return [], warnings, institution_name


async def _fetch_faculty_openalex(
    *, university: str, research_interests: list[str]
) -> tuple[list[dict[str, Any]], list[dict], str | None]:
    """Discover faculty via OpenAlex institution + author search."""
    warnings: list[dict] = []

    institution = await openalex.resolve_institution(query=university)
    if not institution:
        warnings.append({
            "stage": "openalex_institution",
            "error": f"could not resolve institution: {university}",
        })
        return [], warnings, None

    institution_id = institution["id"]
    institution_name = institution["display_name"]
    logger.info(
        "resolved institution: %s (id=%s)", institution_name, institution_id
    )

    # Try with topic filter first
    authors = await openalex.get_authors_by_institution(
        institution_id=institution_id,
        topics=research_interests,
        limit=50,
    )

    # If topic-filtered search returns too few results, retry without topic filter
    if len(authors) < 10:
        logger.info(
            "topic-filtered search returned only %d authors, retrying without topic filter",
            len(authors),
        )
        broader_authors = await openalex.get_authors_by_institution(
            institution_id=institution_id,
            topics=None,
            limit=50,
        )
        # Merge: topic-matched first, then broader results (deduped)
        seen_ids = {a["openalex_id"] for a in authors}
        for a in broader_authors:
            if a["openalex_id"] not in seen_ids:
                authors.append(a)
                seen_ids.add(a["openalex_id"])

    if not authors:
        warnings.append({
            "stage": "openalex_authors",
            "error": "no authors found for institution",
        })
        return [], warnings, institution_name

    # Filter: only keep authors whose primary institution matches
    verified_authors = []
    for a in authors:
        institutions = a.get("last_known_institutions", [])
        if not institutions:
            continue
        # Check if the target institution is the primary (first) affiliation
        primary_id = institutions[0].get("id", "")
        if primary_id == institution_id:
            verified_authors.append(a)
        elif any(inst.get("id") == institution_id for inst in institutions):
            # Also include if it's a secondary affiliation
            verified_authors.append(a)

    if not verified_authors and authors:
        # If verification filtered everyone out, use unfiltered (edge case)
        logger.warning(
            "institution verification filtered all %d authors, using unfiltered",
            len(authors),
        )
        verified_authors = authors

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
        for a in verified_authors
    ]

    logger.info(
        "OpenAlex discovery: %d authors found, %d after verification",
        len(authors), len(faculty),
    )
    return faculty, warnings, institution_name


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


_INELIGIBLE_TITLE_KEYWORDS = {"visiting", "adjunct", "emeritus", "honorary"}


def _is_eligible_faculty(title: str | None) -> bool:
    """Check if a faculty member is eligible (not visiting/adjunct/emeritus/honorary)."""
    if not title:
        return True
    title_lower = title.lower()
    return not any(kw in title_lower for kw in _INELIGIBLE_TITLE_KEYWORDS)


def filter_faculty_by_relevance(
    *, faculty_data: list[dict[str, Any]], research_interests: list[str]
) -> list[dict[str, Any]]:
    """Filter faculty by topic overlap with research interests.

    Uses OpenAlex topic hierarchy when available (domain > field > subfield > topic).
    Falls back to keyword matching on name/title/department for scraped faculty.
    Excludes visiting/adjunct/emeritus/honorary faculty.
    Returns top 20 by score.
    """
    # Remove ineligible faculty first
    faculty_data = [f for f in faculty_data if _is_eligible_faculty(f.get("title"))]

    if len(faculty_data) <= 20:
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
    return [f for f, _ in scored[:20]]


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

    # check cache by openalex_id first, then by name+university
    if openalex_id:
        if cached := await get_cached_professor_by_openalex_id(openalex_id=openalex_id):
            return cached
    if cached := await get_cached_professor(name=name, university=university):
        return cached

    # OpenAlex primary path: author already has metrics from discovery
    if openalex_id:
        return await _enrich_from_openalex(
            faculty=faculty,
            university=university,
            university_url=university_url,
            pre_fetched_works=all_works.get(openalex_id) if all_works else None,
        )

    # fallback: scrape professor profile page, try OpenAlex name lookup
    return await _enrich_from_scraping(
        faculty=faculty, university=university, university_url=university_url
    )


async def _enrich_from_openalex(
    *,
    faculty: dict[str, Any],
    university: str,
    university_url: str,
    pre_fetched_works: list[dict] | None = None,
) -> ProfessorProfile:
    """Enrich professor using OpenAlex works API."""
    openalex_id = faculty["openalex_id"]

    # Use pre-fetched works if available (from batch), otherwise fetch individually
    if pre_fetched_works is not None:
        works = pre_fetched_works
    else:
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
    research_areas = _clean_research_areas(faculty.get("topics", [])[:7])

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
    *, faculty: dict[str, Any], university: str, university_url: str
) -> ProfessorProfile:
    """Fallback enrichment: scrape professor profile page with Gemini.

    Also attempts OpenAlex name lookup to get publications and citation metrics.
    """
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

    # Try OpenAlex name lookup to get publications and citations
    publications: list[Publication] = []
    citation_metrics: CitationMetrics | None = None
    openalex_id: str | None = None
    openalex_topics: list[str] = []

    try:
        # Resolve institution for name search
        institution = await openalex.resolve_institution(query=university_url or university)
        institution_id = institution["id"] if institution else None

        author = await openalex.search_author_by_name(
            name=name, institution_id=institution_id
        )
        if author:
            openalex_id = author["openalex_id"]
            openalex_topics = author.get("topics", [])[:7]
            citation_metrics = CitationMetrics(
                h_index=author.get("h_index", 0),
                i10_index=author.get("i10_index", 0),
                total_citations=author.get("cited_by_count", 0),
            )
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
    except Exception as e:
        logger.debug("OpenAlex name lookup failed for %s: %s", name, e)

    # Use OpenAlex topics if available, otherwise scraped research areas
    research_areas = extra_data.get("research_areas") or []
    if isinstance(research_areas, str):
        research_areas = [r.strip() for r in research_areas.split(",") if r.strip()]
    if openalex_topics:
        research_areas = openalex_topics
    research_areas = _clean_research_areas(research_areas)

    prof = ProfessorProfile(
        id=uuid4(),
        name=name,
        title=faculty.get("title") or extra_data.get("title"),
        department=faculty.get("department") or extra_data.get("department"),
        university=university,
        email=faculty.get("email") or extra_data.get("email"),
        openalex_id=openalex_id,
        research_areas=research_areas,
        publications=publications,
        citation_metrics=citation_metrics,
        last_updated=datetime.now(UTC),
    )
    await cache_professor(profile=prof)
    return prof


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
        # Skip very short entries
        if len(area) < 3:
            continue
        # Skip if it's just a stopword
        if area.lower() in stopwords:
            continue
        cleaned.append(area)
    return cleaned


async def enrich_professors(
    *,
    faculty_data: list[dict[str, Any]],
    university: str,
    university_url: str,
) -> tuple[list[ProfessorProfile], list[dict]]:
    """Enrich professor profiles with publication data (concurrent).

    Uses batched OpenAlex works API for professors with openalex_ids.
    Returns (professors, errors).
    """
    # Batch cache lookup: single DB query instead of 2 per professor
    lookups = [
        (f.get("openalex_id"), f.get("name", ""), university)
        for f in faculty_data
    ]
    cached_map = await get_cached_professors_batch(lookups=lookups)

    # Separate cached hits from misses
    professors: list[ProfessorProfile] = []
    uncached_faculty: list[dict[str, Any]] = []
    for f in faculty_data:
        oa_id = f.get("openalex_id")
        name = f.get("name", "")
        cached = None
        if oa_id:
            cached = cached_map.get(oa_id)
        if not cached:
            cached = cached_map.get(f"{name}|{university}")
        if cached:
            professors.append(cached)
        else:
            uncached_faculty.append(f)

    # Batch-fetch works for all uncached OpenAlex professors at once
    openalex_ids = [
        f["openalex_id"] for f in uncached_faculty
        if f.get("openalex_id")
    ]
    all_works: dict[str, list[dict]] = {}
    if openalex_ids:
        try:
            all_works = await openalex.get_works_for_authors(
                author_ids=openalex_ids, limit_per_author=20, years=5
            )
        except Exception as e:
            logger.warning("batch works fetch failed, falling back to individual: %s", e)

    semaphore = asyncio.Semaphore(20)

    async def enrich_with_limit(faculty: dict[str, Any]) -> ProfessorProfile | None:
        async with semaphore:
            return await enrich_single_professor(
                faculty=faculty,
                university=university,
                university_url=university_url,
                all_works=all_works,
            )

    tasks = [enrich_with_limit(f) for f in uncached_faculty]
    results = await asyncio.gather(*tasks, return_exceptions=True)

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

    # Post-process: detect and clear duplicate research areas (page-level noise)
    _deduplicate_research_areas(professors)

    return professors, errors


def _deduplicate_research_areas(professors: list[ProfessorProfile]) -> None:
    """Detect research areas that are identical across 3+ professors (page-level noise).

    Clears the research_areas for affected professors.
    """
    if len(professors) < 3:
        return

    # Count how many professors share the exact same research areas list
    from collections import Counter
    area_counts: Counter[str] = Counter()
    for p in professors:
        if p.research_areas:
            key = "|".join(sorted(a.lower() for a in p.research_areas))
            area_counts[key] += 1

    # Find noise patterns (same areas shared by 3+ professors)
    noise_keys = {key for key, count in area_counts.items() if count >= 3}
    if not noise_keys:
        return

    logger.info("detected %d noise research area patterns, clearing affected professors", len(noise_keys))
    for p in professors:
        if p.research_areas:
            key = "|".join(sorted(a.lower() for a in p.research_areas))
            if key in noise_keys:
                p.research_areas = []


# ===================================================================
# Post-Match Supplementary Enrichment (Serper — only for selected matches)
# ===================================================================


async def post_enrich_matches(
    *, matches: list[MatchResult], university_url: str
) -> None:
    """Fetch supplementary data for matched professors only.

    Runs Serper lookups (Google Scholar URL, email, directory_url) concurrently
    for only the selected top matches, not all candidates.
    """
    if not matches:
        return

    domain = _extract_domain(university_url) if university_url else ""

    async def enrich_one(match: MatchResult) -> None:
        prof = match.professor
        name = prof.name
        university = prof.university

        # Skip lookups for data we already have
        need_scholar = not prof.google_scholar_url
        need_contact = not prof.email or not prof.directory_url

        tasks = {}
        if need_scholar:
            tasks["scholar"] = tools.search_google_scholar_url(
                name=name, university=university
            )
        if need_contact and domain:
            tasks["contact"] = tools.search_professor_contact(
                name=name, university_domain=domain
            )

        if not tasks:
            return

        results = await asyncio.gather(
            *tasks.values(), return_exceptions=True
        )
        result_map = dict(zip(tasks.keys(), results))

        if "scholar" in result_map:
            scholar_url = result_map["scholar"]
            if isinstance(scholar_url, str):
                prof.google_scholar_url = scholar_url

        if "contact" in result_map:
            contact_info = result_map["contact"]
            if isinstance(contact_info, dict):
                if not prof.email:
                    prof.email = contact_info.get("email")
                if not prof.directory_url:
                    prof.directory_url = contact_info.get("homepage")

    await asyncio.gather(
        *(enrich_one(m) for m in matches), return_exceptions=True
    )


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
            "research_areas": p.research_areas[:5],
            "recent_papers": [pub.title for pub in p.publications[:3]],
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

            # Fuzzy match: case-insensitive substring matching for publications
            relevant_pubs = []
            gemini_titles_lower = [t.lower() for t in m.relevant_publication_titles]
            for p in prof.publications:
                p_title_lower = p.title.lower()
                for gt in gemini_titles_lower:
                    if gt in p_title_lower or p_title_lower in gt:
                        relevant_pubs.append(p)
                        break

            # Fallback: if no fuzzy matches, use top publications by citation count
            if not relevant_pubs and prof.publications:
                relevant_pubs = sorted(
                    prof.publications, key=lambda x: x.citation_count, reverse=True
                )[:5]

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
