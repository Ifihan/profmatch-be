from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.models import CleanupResponse, MessageResponse, SessionData, SessionResponse
from app.services.session_store import delete_session, get_session, set_session
from app.utils.storage import cleanup_old_sessions, delete_session_files

router = APIRouter(prefix="/api/session", tags=["session"])


@router.post("", response_model=SessionResponse)
async def create_session():
    """Create a new matching session."""
    session_id = str(uuid4())
    await set_session(session_id=session_id, data={"status": "created"})
    return SessionResponse(session_id=session_id)


@router.get("/{session_id}", response_model=SessionData)
async def get_session_data(session_id: str):
    """Get session status and data."""
    if not (data := await get_session(session_id=session_id)):
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionData(session_id=session_id, **data)


@router.delete("/{session_id}", response_model=MessageResponse)
async def delete_session_data(session_id: str):
    """Delete session and associated data."""
    if not await delete_session(session_id=session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    await delete_session_files(session_id)

    return MessageResponse(message="Session deleted")


@router.post("/cleanup", response_model=CleanupResponse)
async def trigger_cleanup():
    """Manually trigger cleanup of old sessions (admin endpoint)."""
    from app.config import settings

    cleaned_count = await cleanup_old_sessions(hours=settings.session_ttl_hours)
    return CleanupResponse(
        message="Cleanup completed",
        sessions_cleaned=cleaned_count,
        ttl_hours=settings.session_ttl_hours,
    )
