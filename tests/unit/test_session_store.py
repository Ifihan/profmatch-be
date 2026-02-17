"""Unit tests for app/services/session_store.py."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.database import Base, Session


# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests
# ---------------------------------------------------------------------------

_test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_test_session = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def patch_session_maker():
    """Redirect session_store to use the in-memory DB."""
    with patch("app.services.session_store.async_session", _test_session):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSetAndGetSession:
    """Tests for set_session + get_session round-trip."""

    @pytest.mark.asyncio
    async def test_set_and_get_returns_data(self):
        """Basic set then get works."""
        from app.services.session_store import get_session, set_session

        await set_session(session_id="s1", data={"status": "created"})
        result = await get_session(session_id="s1")

        assert result == {"status": "created"}

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        """Getting a non-existent session returns None."""
        from app.services.session_store import get_session

        result = await get_session(session_id="nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_overwrites_data(self):
        """Calling set_session twice upserts."""
        from app.services.session_store import get_session, set_session

        await set_session(session_id="s1", data={"v": 1})
        await set_session(session_id="s1", data={"v": 2})
        result = await get_session(session_id="s1")

        assert result == {"v": 2}

    @pytest.mark.asyncio
    async def test_complex_data_roundtrip(self):
        """Complex nested data survives JSON round-trip."""
        from app.services.session_store import get_session, set_session

        data = {
            "status": "matching",
            "university": "MIT",
            "research_interests": ["AI", "ML", "NLP"],
            "file_ids": ["file1", "file2"],
            "match_progress": 50,
            "current_step": "Enriching professors",
        }
        await set_session(session_id="complex", data=data)
        result = await get_session(session_id="complex")

        assert result == data

    @pytest.mark.asyncio
    async def test_nested_data_roundtrip(self):
        """Session with nested lists/dicts works."""
        from app.services.session_store import get_session, set_session

        data = {
            "status": "completed",
            "match_results": [
                {"professor": "Dr. Smith", "score": 85.5},
                {"professor": "Dr. Jones", "score": 78.2},
            ],
        }
        await set_session(session_id="nested", data=data)
        result = await get_session(session_id="nested")

        assert result == data
        assert len(result["match_results"]) == 2


class TestDeleteSession:
    """Tests for delete_session."""

    @pytest.mark.asyncio
    async def test_delete_existing_returns_true(self):
        """Deleting an existing session returns True."""
        from app.services.session_store import delete_session, set_session

        await set_session(session_id="s1", data={"x": 1})
        result = await delete_session(session_id="s1")

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self):
        """Deleting a non-existent session returns False."""
        from app.services.session_store import delete_session

        result = await delete_session(session_id="nope")

        assert result is False

    @pytest.mark.asyncio
    async def test_get_after_delete_returns_none(self):
        """Session is gone after deletion."""
        from app.services.session_store import delete_session, get_session, set_session

        await set_session(session_id="s1", data={"x": 1})
        await delete_session(session_id="s1")
        result = await get_session(session_id="s1")

        assert result is None


class TestExpiredSessions:
    """Tests for expiry behaviour."""

    @pytest.mark.asyncio
    async def test_expired_session_returns_none(self):
        """Expired session is not returned by get_session."""
        from app.services.session_store import get_session

        # Insert a row that is already expired
        async with _test_session() as db:
            db.add(Session(
                id="expired",
                data={"x": 1},
                expires_at=datetime.utcnow() - timedelta(hours=1),
            ))
            await db.commit()

        result = await get_session(session_id="expired")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_expired_sessions(self):
        """delete_expired_sessions removes only expired rows."""
        from app.services.session_store import delete_expired_sessions, get_session, set_session

        # One valid session
        await set_session(session_id="valid", data={"ok": True})

        # One expired session (insert directly)
        async with _test_session() as db:
            db.add(Session(
                id="old",
                data={"old": True},
                expires_at=datetime.utcnow() - timedelta(hours=1),
            ))
            await db.commit()

        count = await delete_expired_sessions()
        assert count == 1

        # Valid still there, old gone
        assert await get_session(session_id="valid") is not None
        assert await get_session(session_id="old") is None
