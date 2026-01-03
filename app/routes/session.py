from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.redis import delete_session, get_session, set_session

router = APIRouter(prefix="/api/session", tags=["session"])


class SessionResponse(BaseModel):
    """Session creation response."""
    session_id: str


class SessionData(BaseModel):
    """Session data response."""
    session_id: str
    university: str | None = None
    research_interests: list[str] = []
    file_ids: list[str] = []
    status: str = "created"


@router.post("", response_model=SessionResponse)
async def create_session():
    """Create a new matching session."""
    session_id = str(uuid4())
    await set_session(session_id, {"status": "created"})
    return SessionResponse(session_id=session_id)


@router.get("/{session_id}", response_model=SessionData)
async def get_session_data(session_id: str):
    """Get session status and data."""
    data = await get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionData(session_id=session_id, **data)


@router.delete("/{session_id}")
async def delete_session_data(session_id: str):
    """Delete session and associated data."""
    deleted = await delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"message": "Session deleted"}
