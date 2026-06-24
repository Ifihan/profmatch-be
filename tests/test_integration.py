"""DB-backed integration tests (skip when Postgres is unreachable — see conftest).

These exercise the real ledger advisory locks and promo redemption against the
configured database, with per-test cleanup via the `track` fixture.
"""
import asyncio
import uuid

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import create_access_token
from app.models import User
from app.services import credits


def _auth(uid: str, admin: bool = False) -> dict:
    return {"Authorization": f"Bearer {create_access_token(uid, admin)}"}


async def test_auth_flow(client, track):
    email = f"test_{uuid.uuid4().hex[:10]}@example.com"
    r = await client.post("/api/auth/signup", json={
        "name": "T", "email": email, "password": "password123", "confirm_password": "password123",
    })
    assert r.status_code == 201
    async with SessionLocal() as db:
        uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    track["users"].append(uid)

    assert (await client.post("/api/auth/login", json={"email": email, "password": "password123"})).status_code == 200
    assert (await client.post("/api/auth/login", json={"email": email, "password": "wrong"})).status_code == 401
    assert (await client.post("/api/auth/forgot-password", json={"email": email})).status_code == 200


async def test_me_and_search_history(client, make_user, track):
    uid = await make_user(starting_credits=2)
    r = await client.get("/api/auth/me", headers=_auth(uid))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == uid and body["credit_balance"] == 2 and "email" in body

    # seed a finished search owned by the user
    async with SessionLocal() as db:
        from app.models import MatchJob, JobStatus
        job = MatchJob(
            user_id=uid, university_url="http://u.edu", research_interests="x",
            cv_text="cv", status=JobStatus.DONE, progress=100,
            results=[{"professor": {"id": "p1", "name": "Dr X", "research_areas": [], "publications": []},
                      "match_score": 0.9}],
            total_analyzed=10, processing_seconds=3.2,
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    r = await client.get("/api/auth/me/searches", headers=_auth(uid))
    assert r.status_code == 200
    rows = r.json()
    assert any(s["job_id"] == job_id and s["match_count"] == 1 for s in rows)

    r = await client.get(f"/api/auth/me/searches/{job_id}", headers=_auth(uid))
    assert r.status_code == 200 and r.json()["result"]["matches"][0]["professor"]["name"] == "Dr X"

    # another user can't read it
    other = await make_user(starting_credits=0)
    assert (await client.get(f"/api/auth/me/searches/{job_id}", headers=_auth(other))).status_code == 404


async def test_concurrent_spend_allows_only_one(make_user):
    uid = await make_user(starting_credits=1)

    async def spend(ref):
        async with SessionLocal() as db:
            ok = await credits.try_spend(db, uid, reference=ref)
            await db.commit()
            return ok

    r1, r2 = await asyncio.gather(spend("a"), spend("b"))
    assert sorted([r1, r2]) == [False, True]
    async with SessionLocal() as db:
        assert await credits._raw_balance(db, uid) == 0


async def test_credits_endpoint(client, make_user):
    uid = await make_user(starting_credits=2)
    r = await client.get("/api/credits", headers=_auth(uid))
    assert r.status_code == 200 and r.json()["balance"] == 2


async def test_credit_plans_stub(client, make_user):
    uid = await make_user(starting_credits=0)
    r = await client.get("/api/credits/plans", headers=_auth(uid))
    assert r.status_code == 200 and r.json()["available"] is False


async def test_promo_redemption_and_one_per_user(client, make_user, track):
    admin = await make_user(admin=True, starting_credits=0)
    u1 = await make_user(starting_credits=1)
    code = f"P{uuid.uuid4().hex[:8]}"
    r = await client.post("/api/admin/promo", json={"code": code, "credits": 5, "max_redemptions": 1},
                          headers=_auth(admin, True))
    assert r.status_code == 201
    track["promos"].append(r.json()["id"])

    r = await client.post("/api/promo/redeem", json={"code": code}, headers=_auth(u1))
    assert r.status_code == 200 and r.json()["balance"] == 6
    assert (await client.post("/api/promo/redeem", json={"code": code}, headers=_auth(u1))).status_code == 409
    assert (await client.post("/api/promo/redeem", json={"code": "NOPE"}, headers=_auth(u1))).status_code == 404


async def test_concurrent_redemption_respects_cap(client, make_user, track):
    admin = await make_user(admin=True, starting_credits=0)
    u1 = await make_user(starting_credits=0)
    u2 = await make_user(starting_credits=0)
    code = f"R{uuid.uuid4().hex[:8]}"
    r = await client.post("/api/admin/promo", json={"code": code, "credits": 3, "max_redemptions": 1},
                          headers=_auth(admin, True))
    track["promos"].append(r.json()["id"])

    rs = await asyncio.gather(
        client.post("/api/promo/redeem", json={"code": code}, headers=_auth(u1)),
        client.post("/api/promo/redeem", json={"code": code}, headers=_auth(u2)),
    )
    assert sorted(x.status_code for x in rs) == [200, 409]


async def _seed_job(uid: str, status=None) -> str:
    from app.models import MatchJob, JobStatus
    async with SessionLocal() as db:
        job = MatchJob(
            user_id=uid, university_url="http://u.edu", research_interests="x",
            cv_text="cv", status=status or JobStatus.DONE, progress=100,
        )
        db.add(job)
        await db.commit()
        return job.id


async def test_update_name(client, make_user):
    uid = await make_user(starting_credits=2)
    r = await client.patch("/api/auth/me", json={"name": "Renamed User"}, headers=_auth(uid))
    assert r.status_code == 200 and r.json()["name"] == "Renamed User"
    # persisted
    assert (await client.get("/api/auth/me", headers=_auth(uid))).json()["name"] == "Renamed User"
    # empty name rejected
    assert (await client.patch("/api/auth/me", json={"name": ""}, headers=_auth(uid))).status_code == 422


async def test_delete_one_search(client, make_user):
    uid = await make_user(starting_credits=0)
    job_id = await _seed_job(uid)
    other = await make_user(starting_credits=0)

    # a different user can't delete it
    assert (await client.delete(f"/api/auth/me/searches/{job_id}", headers=_auth(other))).status_code == 404
    # owner can
    assert (await client.delete(f"/api/auth/me/searches/{job_id}", headers=_auth(uid))).status_code == 204
    assert (await client.get(f"/api/auth/me/searches/{job_id}", headers=_auth(uid))).status_code == 404


async def test_clear_history_only_terminal_and_own(client, make_user):
    from app.models import JobStatus
    uid = await make_user(starting_credits=0)
    done = await _seed_job(uid)
    active = await _seed_job(uid, status=JobStatus.RANKING)
    other_job = await _seed_job(await make_user(starting_credits=0))

    assert (await client.delete("/api/auth/me/searches", headers=_auth(uid))).status_code == 204

    rows = (await client.get("/api/auth/me/searches", headers=_auth(uid))).json()
    ids = {r["job_id"] for r in rows}
    assert done not in ids        # finished search cleared
    assert active in ids          # in-flight search untouched
    # another user's history is unaffected
    async with SessionLocal() as db:
        from app.models import MatchJob
        assert (await db.execute(select(MatchJob.id).where(MatchJob.id == other_job))).scalar_one_or_none() == other_job


async def test_delete_account(client, make_user, track):
    uid = await make_user(starting_credits=3)  # leaves a credit_events row
    await _seed_job(uid)
    other = await make_user(starting_credits=1)

    # wrong password is rejected
    assert (await client.request(
        "DELETE", "/api/auth/me", json={"password": "wrong"}, headers=_auth(uid)
    )).status_code == 401

    r = await client.request(
        "DELETE", "/api/auth/me", json={"password": "password123"}, headers=_auth(uid)
    )
    assert r.status_code == 204

    # the user and all their rows are gone; the token no longer resolves
    assert (await client.get("/api/auth/me", headers=_auth(uid))).status_code == 401
    async with SessionLocal() as db:
        from app.models import MatchJob, CreditEvent
        assert (await db.execute(select(User.id).where(User.id == uid))).scalar_one_or_none() is None
        assert (await db.execute(select(MatchJob.id).where(MatchJob.user_id == uid))).first() is None
        assert (await db.execute(select(CreditEvent.id).where(CreditEvent.user_id == uid))).first() is None
        # the other user is untouched
        assert (await db.execute(select(User.id).where(User.id == other))).scalar_one() == other


async def test_admin_requires_admin(client, make_user):
    u1 = await make_user(starting_credits=0)
    admin = await make_user(admin=True, starting_credits=0)
    # is_admin is read from the DB, not the JWT claim — a forged claim won't pass.
    assert (await client.get("/api/admin/users", headers=_auth(u1, admin=True))).status_code == 403
    assert (await client.get("/api/admin/metrics", headers=_auth(admin, admin=True))).status_code == 200
