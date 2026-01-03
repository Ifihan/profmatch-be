import logging
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.models import MatchResult
from app.services.orchestrator import run_matching
from app.services.redis import get_session, set_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/match", tags=["match"])


class MatchRequest(BaseModel):
    """Request to start matching process."""
    session_id: str
    university: str
    research_interests: list[str]
    file_ids: list[str] = []


class MatchStatusResponse(BaseModel):
    """Match progress status."""
    match_id: str
    status: str
    progress: int
    current_step: str | None = None


class MatchResultsResponse(BaseModel):
    """Match results response."""
    match_id: str
    status: str
    results: list[MatchResult] = []


async def run_matching_task(
    session_id: str,
    university: str,
    research_interests: list[str],
    file_ids: list[str],
) -> None:
    """Background task to run matching."""
    try:
        await run_matching(session_id, university, research_interests, file_ids)
    except Exception as e:
        logger.exception(f"Matching failed: {e}")
        session = await get_session(session_id)
        if session:
            session["match_status"] = "failed"
            session["current_step"] = f"Error: {str(e)[:100]}"
            await set_session(session_id, session)


@router.post("", response_model=MatchStatusResponse)
async def start_match(request: MatchRequest, background_tasks: BackgroundTasks):
    """Initiate matching process."""
    session = await get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    match_id = str(uuid4())

    session["university"] = request.university
    session["research_interests"] = request.research_interests
    session["match_id"] = match_id
    session["match_status"] = "processing"
    session["match_progress"] = 0
    session["current_step"] = "Initializing"
    await set_session(request.session_id, session)

    background_tasks.add_task(
        run_matching_task,
        request.session_id,
        request.university,
        request.research_interests,
        request.file_ids,
    )

    return MatchStatusResponse(
        match_id=match_id,
        status="processing",
        progress=0,
        current_step="Initializing",
    )


@router.get("/{match_id}/status", response_model=MatchStatusResponse)
async def get_match_status(match_id: str, session_id: str):
    """Check matching progress."""
    session = await get_session(session_id)
    if not session or session.get("match_id") != match_id:
        raise HTTPException(status_code=404, detail="Match not found")

    return MatchStatusResponse(
        match_id=match_id,
        status=session.get("match_status", "unknown"),
        progress=session.get("match_progress", 0),
        current_step=session.get("current_step"),
    )


@router.get("/{match_id}/results", response_model=MatchResultsResponse)
async def get_match_results(match_id: str, session_id: str):
    """Retrieve match results."""
    session = await get_session(session_id)
    if not session or session.get("match_id") != match_id:
        raise HTTPException(status_code=404, detail="Match not found")

    if session.get("match_status") != "completed":
        raise HTTPException(status_code=400, detail="Matching not yet completed")

    results = session.get("match_results", [])
    return MatchResultsResponse(
        match_id=match_id,
        status="completed",
        results=[MatchResult(**r) for r in results],
    )
