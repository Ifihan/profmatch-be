"""User-facing promo redemption. Admin promo CRUD lives in routes/admin.py."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import CreditEventType, PromoCode, PromoRedemption, User
from app.services import credits

router = APIRouter(prefix="/promo", tags=["promo"])


class RedeemRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"code": "WELCOME2025"}})

    code: str


@router.post("/redeem")
async def redeem(
    body: RedeemRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    promo = (
        await db.execute(select(PromoCode).where(PromoCode.code == body.code))
    ).scalar_one_or_none()
    if promo is None or promo.is_disabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid promo code")
    if promo.expires_at is not None:
        expires = promo.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_410_GONE, "Promo code expired")

    # Serialize redemptions of this code so concurrent calls can't exceed the cap.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"promo:{promo.id}"}
    )
    # Re-read under the lock — times_redeemed may have changed since the load above.
    await db.refresh(promo)
    if promo.max_redemptions is not None and promo.times_redeemed >= promo.max_redemptions:
        raise HTTPException(status.HTTP_409_CONFLICT, "Promo code fully redeemed")

    already = (
        await db.execute(
            select(PromoRedemption.id).where(
                PromoRedemption.promo_id == promo.id,
                PromoRedemption.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Promo code already redeemed")

    db.add(PromoRedemption(promo_id=promo.id, user_id=user.id))
    promo.times_redeemed += 1
    await credits.grant(db, user.id, promo.credits, CreditEventType.GRANT_PROMO, reference=promo.code)
    balance = await credits.get_balance(db, user.id)
    await db.commit()
    return {"credits_granted": promo.credits, "balance": balance}
