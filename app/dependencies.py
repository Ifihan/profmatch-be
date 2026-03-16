"""Shared FastAPI dependencies."""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Query

from app.config import Settings, settings
from app.services.session_store import get_session


def get_settings() -> Settings:
    """Return the settings singleton (overridable in tests)."""
    return settings


async def require_session(session_id: str = Query(...)) -> dict[str, Any]:
    """Fetch session by ID, raising 404 if missing or expired."""
    if not (session := await get_session(session_id=session_id)):
        raise HTTPException(status_code=404, detail="Session not found")
    return session


ActiveSession = Annotated[dict[str, Any], Depends(require_session)]
