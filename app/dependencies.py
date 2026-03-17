"""Shared FastAPI dependencies."""

from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Query

from app.config import Settings, settings
from app.models.database import User
from app.services.auth import decode_access_token, get_user_by_id
from app.services.session_store import get_session


def get_settings() -> Settings:
    """Return the settings singleton (overridable in tests)."""
    return settings


async def require_session(session_id: str = Query(...)) -> dict[str, Any]:
    """Fetch session by ID, raising 404 if missing or expired."""
    if not (session := await get_session(session_id=session_id)):
        raise HTTPException(status_code=404, detail="Session not found")
    return session


async def get_current_user_optional(authorization: str | None = Header(None)) -> User | None:
    """Extract user from Bearer token. Returns None if no token or invalid."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ")
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    return await get_user_by_id(payload["sub"])


async def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    """Require a valid authenticated user or raise 401."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


ActiveSession = Annotated[dict[str, Any], Depends(require_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
