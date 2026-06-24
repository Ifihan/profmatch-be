from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import require_admin
from app.core.db import get_db
from app.models import (
    User, MatchJob, CreditEvent, CreditEventType, PromoCode, PromoRedemption,
)
from app.schemas.admin import (
    MetricsResponse, PromoResponse, CreatePromoResponse, PromoToggleResponse,
    OkResponse, UserSummary, UserDetail,
)
from app.services import credits

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(db: AsyncSession = Depends(get_db)):
    # All four counts as scalar subqueries in one SELECT — a single round-trip.
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    total_users = select(func.count(User.id)).scalar_subquery()
    total_searches = select(func.count(MatchJob.id)).scalar_subquery()
    paid_users = (
        select(func.count(func.distinct(CreditEvent.user_id)))
        .where(CreditEvent.event_type == CreditEventType.GRANT_PURCHASE)
        .scalar_subquery()
    )
    active_users = (
        select(func.count(func.distinct(MatchJob.user_id)))
        .where(MatchJob.user_id.is_not(None), MatchJob.created_at >= cutoff)
        .scalar_subquery()
    )
    row = (await db.execute(select(
        total_users.label("total_users"),
        active_users.label("active_users"),
        total_searches.label("total_searches"),
        paid_users.label("paid_users"),
    ))).one()
    return {
        "total_users": row.total_users,
        "active_users": row.active_users,
        "total_searches": row.total_searches,
        "paid_users": row.paid_users,
    }


class CreatePromo(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "code": "WELCOME2025", "credits": 5, "max_redemptions": 100,
    }})

    code: str
    credits: int
    max_redemptions: int | None = None


@router.post("/promo", status_code=201, response_model=CreatePromoResponse)
async def create_promo(body: CreatePromo, db: AsyncSession = Depends(get_db)):
    promo = PromoCode(code=body.code, credits=body.credits, max_redemptions=body.max_redemptions)
    db.add(promo)
    await db.commit()
    return {"id": promo.id, "code": promo.code}


@router.get("/promo", response_model=list[PromoResponse])
async def list_promos(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(PromoCode))).scalars().all()
    return [
        {"id": p.id, "code": p.code, "credits": p.credits,
         "max_redemptions": p.max_redemptions,
         "times_redeemed": p.times_redeemed, "is_disabled": p.is_disabled}
        for p in rows
    ]


async def _set_promo_disabled(promo_id: str, disabled: bool, db: AsyncSession) -> dict:
    promo = (await db.execute(select(PromoCode).where(PromoCode.id == promo_id))).scalar_one_or_none()
    if not promo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    promo.is_disabled = disabled
    await db.commit()
    return {"ok": True, "is_disabled": disabled}


@router.patch("/promo/{promo_id}/disable", response_model=PromoToggleResponse)
async def disable_promo(promo_id: str, db: AsyncSession = Depends(get_db)):
    return await _set_promo_disabled(promo_id, True, db)


@router.patch("/promo/{promo_id}/enable", response_model=PromoToggleResponse)
async def enable_promo(promo_id: str, db: AsyncSession = Depends(get_db)):
    return await _set_promo_disabled(promo_id, False, db)


@router.delete("/promo/{promo_id}", response_model=OkResponse)
async def delete_promo(promo_id: str, db: AsyncSession = Depends(get_db)):
    promo = (await db.execute(select(PromoCode).where(PromoCode.id == promo_id))).scalar_one_or_none()
    if not promo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    await db.execute(delete(PromoRedemption).where(PromoRedemption.promo_id == promo_id))
    await db.delete(promo)
    await db.commit()
    return {"ok": True}


@router.get("/users", response_model=list[UserSummary])
async def list_users(
    limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)
):
    rows = (await db.execute(
        select(User).order_by(User.created_at.desc()).limit(min(limit, 200)).offset(offset)
    )).scalars().all()
    return [
        {"id": u.id, "name": u.name, "email": u.email,
         "is_admin": u.is_admin, "is_disabled": u.is_disabled,
         "created_at": u.created_at}
        for u in rows
    ]


@router.get("/users/{user_id}", response_model=UserDetail)
async def get_user(user_id: str, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    balance = await credits.get_balance(db, user.id)
    await db.commit()  # persist any lazy-regen surfaced during the balance read
    return {
        "id": user.id, "name": user.name, "email": user.email,
        "is_admin": user.is_admin, "is_disabled": user.is_disabled,
        "created_at": user.created_at, "credit_balance": balance,
    }


@router.patch("/users/{user_id}/disable", response_model=OkResponse)
async def disable_user(user_id: str, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    user.is_disabled = True
    await db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}", response_model=OkResponse)
async def delete_user(user_id: str, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    # Remove dependent rows first (FK children + the user's match_jobs).
    await db.execute(delete(CreditEvent).where(CreditEvent.user_id == user_id))
    await db.execute(delete(PromoRedemption).where(PromoRedemption.user_id == user_id))
    await db.execute(delete(MatchJob).where(MatchJob.user_id == user_id))
    await db.delete(user)
    await db.commit()
    return {"ok": True}
