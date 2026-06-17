"""Shared test fixtures.

Unit tests (respx-mocked, no DB) run anywhere. DB-backed integration tests depend
on the `track` fixture, which skips the test when the configured Postgres is
unreachable, and cleans up any rows it created afterwards.
"""
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.core.security import hash_password
from app.main import app
from app.models import CreditEventType, User
from app.services import credits


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    # Release pool connections on this test's loop to avoid cross-loop cleanup noise.
    await engine.dispose()


@pytest_asyncio.fixture
async def track():
    """Register created user/promo ids for teardown; skip if DB is unreachable."""
    import pytest
    # pytest-asyncio gives each test its own event loop; rebind the shared engine's
    # pool to the current loop so connections aren't reused across loops.
    await engine.dispose()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"database not reachable: {exc}")

    reg: dict[str, list[str]] = {"users": [], "promos": []}
    yield reg

    async with engine.begin() as conn:
        for uid in reg["users"]:
            await conn.execute(text("delete from credit_events where user_id=:u"), {"u": uid})
            await conn.execute(text("delete from promo_redemptions where user_id=:u"), {"u": uid})
            await conn.execute(text("delete from match_jobs where user_id=:u"), {"u": uid})
            await conn.execute(text("delete from users where id=:u"), {"u": uid})
        for pid in reg["promos"]:
            await conn.execute(text("delete from promo_redemptions where promo_id=:p"), {"p": pid})
            await conn.execute(text("delete from promo_codes where id=:p"), {"p": pid})


@pytest_asyncio.fixture
def make_user(track):
    async def _make(admin: bool = False, starting_credits: int = 1) -> str:
        async with SessionLocal() as db:
            u = User(
                name="t",
                email=f"test_{uuid.uuid4().hex[:10]}@example.com",
                password_hash=hash_password("password123"),
                is_admin=admin,
            )
            db.add(u)
            await db.flush()
            if starting_credits:
                await credits.grant(db, u.id, starting_credits, CreditEventType.GRANT_SIGNUP)
            await db.commit()
            track["users"].append(u.id)
            return u.id
    return _make
