import asyncio
import json
import logging
import time
from typing import Any

from app.models import MatchResult
from app.services.matching.enrichment import enrich_professors, post_enrich_matches
from app.services.matching.faculty import fetch_faculty
from app.services.matching.interests import build_research_interest_profile
from app.services.matching.parsing import parse_student_documents, to_int, to_list, to_str
from app.services.matching.ranking import generate_matches
from app.services.matching.routing import (
    _compute_topic_score,
    _directory_search_terms,
    _extract_domain,
    _infer_matching_route,
    _infer_target_academic_units,
    _merge_faculty_sources,
    _normalize_title,
    shortlist_faculty_for_enrichment,
)
from app.services.session_store import get_session, set_session, update_session_fields

logger = logging.getLogger(__name__)

_ENRICHMENT_SHORTLIST_LIMIT = 12


def _elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


async def _post_enrich_and_persist_results(
    *,
    session_id: str,
    matches: list[MatchResult],
    university_url: str,
) -> None:
    """Populate supplementary links/details after the main results are already available."""
    try:
        await post_enrich_matches(matches=matches, university_url=university_url)
        session = await get_session(session_id=session_id)
        if not session or session.get("match_status") != "completed":
            return
        session["match_results"] = [match.model_dump(mode="json") for match in matches]
        await set_session(session_id=session_id, data=session)
    except Exception:
        logger.exception("background post-enrichment failed", extra={"session_id": session_id})


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
    research_profile = await build_research_interest_profile(research_interests)
    matching_route = research_profile.route_name or _infer_matching_route(research_interests)
    wide_event: dict[str, Any] = {
        "event": "matching_pipeline",
        "session_id": session_id,
        "university": university,
        "research_interests": research_interests,
        "matching_route": matching_route,
        "interest_profile": {
            "normalized_phrases": research_profile.normalized_phrases,
            "keywords": research_profile.keywords[:8],
            "target_units": research_profile.target_units,
        },
        "file_count": len(file_ids) if file_ids else 0,
        "start_time": time.time(),
    }

    try:
        parse_and_discovery_started = time.perf_counter()
        await update_progress(
            session_id=session_id,
            progress=5,
            step="Parsing documents and discovering faculty",
        )

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
            fetch_faculty(
                university=university,
                research_interests=research_interests,
                research_profile=research_profile,
            ),
        )
        wide_event["student_profile_parsed"] = student_profile is not None
        wide_event["faculty_found"] = len(faculty_data)
        wide_event["institution_name"] = institution_name
        wide_event["faculty_source"] = "openalex" if not faculty_warnings else "mixed"
        wide_event["parse_and_discovery_ms"] = _elapsed_ms(parse_and_discovery_started)
        if faculty_warnings:
            wide_event["faculty_warnings"] = faculty_warnings

        filtering_started = time.perf_counter()
        await update_progress(session_id=session_id, progress=25, step="Filtering candidates")
        faculty_data = filter_faculty_by_relevance(
            faculty_data=faculty_data,
            research_interests=research_profile.normalized_phrases or research_interests,
        )
        wide_event["faculty_filtered"] = len(faculty_data)
        wide_event["filtering_ms"] = _elapsed_ms(filtering_started)

        shortlisting_started = time.perf_counter()
        enrichment_candidates = shortlist_faculty_for_enrichment(
            faculty_data=faculty_data,
            research_interests=research_profile.normalized_phrases or research_interests,
            limit=_ENRICHMENT_SHORTLIST_LIMIT,
        )
        wide_event["enrichment_candidates"] = len(enrichment_candidates)
        wide_event["shortlisting_ms"] = _elapsed_ms(shortlisting_started)

        enrichment_started = time.perf_counter()
        await update_progress(
            session_id=session_id,
            progress=30,
            step="Retrieving publication data",
        )

        display_university = institution_name or university
        professors, enrichment_errors = await enrich_professors(
            faculty_data=enrichment_candidates,
            university=display_university,
            university_url=university,
        )
        wide_event["professors_enriched"] = len(professors)
        wide_event["enrichment_ms"] = _elapsed_ms(enrichment_started)
        if enrichment_errors:
            wide_event["enrichment_errors"] = enrichment_errors

        ranking_started = time.perf_counter()
        await update_progress(
            session_id=session_id,
            progress=70,
            step="Analyzing research alignment",
        )
        matches = await generate_matches(
            professors=professors,
            research_interests=research_interests,
            student_profile=student_profile,
            research_profile=research_profile,
        )
        wide_event["matches_generated"] = len(matches)
        wide_event["ranking_ms"] = _elapsed_ms(ranking_started)

        persistence_started = time.perf_counter()
        await update_progress(
            session_id=session_id,
            progress=95,
            step="Finalizing recommendations",
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
            session["match_results"] = [match.model_dump(mode="json") for match in matches]
            await set_session(session_id=session_id, data=session)
        wide_event["session_persist_ms"] = _elapsed_ms(persistence_started)

        if matches:
            asyncio.create_task(
                _post_enrich_and_persist_results(
                    session_id=session_id,
                    matches=matches,
                    university_url=university,
                )
            )

        wide_event["outcome"] = "success"
        wide_event["status_code"] = 200
        return matches
    except Exception as exc:
        wide_event["outcome"] = "error"
        wide_event["error"] = {"message": str(exc), "type": type(exc).__name__}
        raise
    finally:
        wide_event["duration_ms"] = int((time.time() - wide_event["start_time"]) * 1000)
        logger.info(json.dumps(wide_event, default=str))


def filter_faculty_by_relevance(
    *,
    faculty_data: list[dict[str, Any]],
    research_interests: list[str],
) -> list[dict[str, Any]]:
    """Compatibility wrapper for tests/importers."""
    from app.services.matching.routing import filter_faculty_by_relevance as _filter

    return _filter(
        faculty_data=faculty_data,
        research_interests=research_interests,
    )
