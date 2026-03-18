"""PostgreSQL-backed session store."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from app.config import settings
from app.models.database import Session
from app.services.database import async_session


async def set_session(*, session_id: str, data: dict[str, Any]) -> None:
    """Store session data (upsert with TTL refresh)."""
    expires_at = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    async with async_session() as db:
        stmt = select(Session).where(Session.id == session_id)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.data = data
            existing.expires_at = expires_at
        else:
            db.add(Session(id=session_id, data=data, expires_at=expires_at))

        await db.commit()


async def get_session(*, session_id: str) -> dict[str, Any] | None:
    """Retrieve session data. Returns None if missing or expired."""
    async with async_session() as db:
        stmt = select(Session).where(
            Session.id == session_id,
            Session.expires_at > datetime.now(UTC),
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        return dict(row.data) if row else None


async def update_session_fields(*, session_id: str, updates: dict[str, Any]) -> None:
    """Update specific fields in session data with a single DB round trip."""
    async with async_session() as db:
        stmt = select(Session).where(
            Session.id == session_id,
            Session.expires_at > datetime.now(UTC),
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            data = dict(existing.data)
            data.update(updates)
            existing.data = data
            await db.commit()


async def delete_session(*, session_id: str) -> bool:
    """Delete session data. Returns True if a row was deleted."""
    async with async_session() as db:
        stmt = delete(Session).where(Session.id == session_id)
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0


async def delete_expired_sessions() -> int:
    """Delete all expired sessions. Returns count deleted."""
    async with async_session() as db:
        stmt = delete(Session).where(Session.expires_at <= datetime.now(UTC))
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount
