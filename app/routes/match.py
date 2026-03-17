import time
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models import MatchRequest, MatchResult, MatchResultsResponse, MatchStatusResponse
from app.models.database import SearchHistory, Session
from app.services.database import async_session
from app.services.orchestrator import run_matching
from app.services.session_store import get_session, set_session

from sqlalchemy import select

router = APIRouter(prefix="/api/match", tags=["match"])


async def _save_search_history(session_id: str, session_data: dict) -> None:
    """Save completed search to history if session belongs to a logged-in user."""
    async with async_session() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session_row = result.scalar_one_or_none()
        if not session_row or not session_row.user_id:
            return

        db.add(SearchHistory(
            user_id=session_row.user_id,
            match_id=session_data.get("match_id", ""),
            university=session_data.get("university", ""),
            research_interests=session_data.get("research_interests", []),
            results=session_data.get("match_results", []),
            total_time=session_data.get("total_match_time"),
        ))
        await db.commit()


async def run_matching_task(
    session_id: str,
    university: str,
    research_interests: list[str],
    file_ids: list[str],
) -> None:
    """Background task to run matching."""
    try:
        await run_matching(
            session_id=session_id,
            university=university,
            research_interests=research_interests,
            file_ids=file_ids,
        )
        # Save to search history for logged-in users
        if session := await get_session(session_id=session_id):
            if session.get("match_status") == "completed":
                await _save_search_history(session_id, session)
    except Exception as e:
        if session := await get_session(session_id=session_id):
            total_time = time.time() - session.get("match_start_time", time.time())
            session["match_status"] = "failed"
            session["current_step"] = f"Error: {str(e)[:100]}"
            session["total_match_time"] = total_time
            await set_session(session_id=session_id, data=session)


@router.post("", response_model=MatchStatusResponse)
async def start_match(request: MatchRequest, background_tasks: BackgroundTasks):
    """Initiate matching process."""
    if not (session := await get_session(session_id=request.session_id)):
        raise HTTPException(status_code=404, detail="Session not found")

    match_id = str(uuid4())

    session["university"] = request.university
    session["research_interests"] = request.research_interests
    session["match_id"] = match_id
    session["match_status"] = "processing"
    session["match_progress"] = 0
    session["current_step"] = "Initializing"
    session["match_start_time"] = time.time()
    await set_session(session_id=request.session_id, data=session)

    background_tasks.add_task(
        run_matching_task,
        session_id=request.session_id,
        university=request.university,
        research_interests=request.research_interests,
        file_ids=request.file_ids,
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
    if not (session := await get_session(session_id=session_id)) or session.get("match_id") != match_id:
        raise HTTPException(status_code=404, detail="Match not found")

    elapsed_time = None
    if session.get("match_start_time"):
        if session.get("total_match_time"):
            elapsed_time = session["total_match_time"]
        else:
            elapsed_time = time.time() - session["match_start_time"]

    return MatchStatusResponse(
        match_id=match_id,
        status=session.get("match_status", "unknown"),
        progress=session.get("match_progress", 0),
        current_step=session.get("current_step"),
        elapsed_time=elapsed_time,
    )


@router.get("/{match_id}/results", response_model=MatchResultsResponse)
async def get_match_results(match_id: str, session_id: str):
    """Retrieve match results."""
    if not (session := await get_session(session_id=session_id)) or session.get("match_id") != match_id:
        raise HTTPException(status_code=404, detail="Match not found")

    if session.get("match_status") != "completed":
        raise HTTPException(status_code=400, detail="Matching not yet completed")

    return MatchResultsResponse(
        match_id=match_id,
        status="completed",
        results=[MatchResult(**r) for r in session.get("match_results", [])],
        total_time=session.get("total_match_time"),
    )
