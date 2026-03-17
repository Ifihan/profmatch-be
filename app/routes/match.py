import time
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Header, Request
from sqlalchemy import select

from app.models import MatchRequest, MatchResult, MatchResultsResponse, MatchStatusResponse
from app.models.database import SearchHistory, Session
from app.services.auth import decode_access_token
from app.services.credits import (
    check_anonymous_limit,
    deduct_credit,
    get_or_create_credits,
    next_free_credit_at,
    record_anonymous_search,
)
from app.services.database import async_session
from app.services.orchestrator import run_matching
from app.services.session_store import get_session, set_session

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


async def _get_user_id_from_token(authorization: str | None) -> str | None:
    """Extract user_id from Bearer token if valid."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    payload = decode_access_token(authorization.removeprefix("Bearer "))
    if not payload or "sub" not in payload:
        return None
    return payload["sub"]


@router.post("", response_model=MatchStatusResponse)
async def start_match(
    match_request: MatchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(None),
):
    """Initiate matching process."""
    if not (session := await get_session(session_id=match_request.session_id)):
        raise HTTPException(status_code=404, detail="Session not found")

    user_id = await _get_user_id_from_token(authorization)
    match_id = str(uuid4())

    if user_id:
        # Authenticated user — check and deduct credits
        success = await deduct_credit(
            user_id=user_id,
            match_id=match_id,
            university=match_request.university,
        )
        if not success:
            credit = await get_or_create_credits(user_id)
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "insufficient_credits",
                    "next_free_credit_at": (
                        next_free_credit_at(credit).isoformat()
                        if next_free_credit_at(credit)
                        else None
                    ),
                },
            )
    else:
        # Anonymous user — enforce IP-based limit
        client_ip = request.client.host if request.client else "unknown"
        if not await check_anonymous_limit(client_ip):
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "anonymous_limit_reached",
                    "message": "Sign up for free to continue searching.",
                },
            )
        await record_anonymous_search(client_ip)
        # Also track in session for frontend display
        session["search_count"] = session.get("search_count", 0) + 1

    session["university"] = match_request.university
    session["research_interests"] = match_request.research_interests
    session["match_id"] = match_id
    session["match_status"] = "processing"
    session["match_progress"] = 0
    session["current_step"] = "Initializing"
    session["match_start_time"] = time.time()
    await set_session(session_id=match_request.session_id, data=session)

    background_tasks.add_task(
        run_matching_task,
        session_id=match_request.session_id,
        university=match_request.university,
        research_interests=match_request.research_interests,
        file_ids=match_request.file_ids,
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
