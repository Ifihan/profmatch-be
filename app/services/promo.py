"""Promo code service: creation, redemption, and management."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.database import (
    PromoCode,
    PromoCodeRedemption,
    SearchCredit,
    SearchHistory,
    User,
)
from app.services.credits import get_or_create_credits
from app.services.database import async_session


async def create_promo_code(
    *, code: str, credits: int, max_uses: int, created_by: str
) -> PromoCode:
    """Create a new promo code."""
    async with async_session() as db:
        existing = await db.execute(
            select(PromoCode).where(PromoCode.code == code)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Promo code already exists")

        promo = PromoCode(
            code=code,
            credits=credits,
            max_uses=max_uses,
            created_by=created_by,
        )
        db.add(promo)
        await db.commit()
        await db.refresh(promo)
        return promo


async def list_promo_codes() -> list[PromoCode]:
    """List all promo codes."""
    async with async_session() as db:
        result = await db.execute(
            select(PromoCode).order_by(PromoCode.created_at.desc())
        )
        return list(result.scalars().all())


async def toggle_promo_code(*, promo_id: str, is_active: bool) -> PromoCode:
    """Enable or disable a promo code."""
    async with async_session() as db:
        result = await db.execute(
            select(PromoCode).where(PromoCode.id == promo_id)
        )
        promo = result.scalar_one_or_none()
        if not promo:
            raise HTTPException(status_code=404, detail="Promo code not found")

        promo.is_active = is_active
        await db.commit()
        await db.refresh(promo)
        return promo


async def delete_promo_code(*, promo_id: str) -> None:
    """Delete a promo code."""
    async with async_session() as db:
        result = await db.execute(
            select(PromoCode).where(PromoCode.id == promo_id)
        )
        promo = result.scalar_one_or_none()
        if not promo:
            raise HTTPException(status_code=404, detail="Promo code not found")

        await db.delete(promo)
        await db.commit()


async def redeem_promo_code(*, code: str, user_id: str) -> tuple[int, int]:
    """Redeem a promo code. Returns (credits_granted, new_balance)."""
    async with async_session() as db:
        result = await db.execute(
            select(PromoCode).where(PromoCode.code == code)
        )
        promo = result.scalar_one_or_none()

        if not promo:
            raise HTTPException(status_code=404, detail="Invalid promo code")
        if not promo.is_active:
            raise HTTPException(status_code=400, detail="Promo code is disabled")
        if promo.use_count >= promo.max_uses:
            raise HTTPException(status_code=400, detail="Promo code has reached its usage limit")

        # Check if user already redeemed this code
        existing = await db.execute(
            select(PromoCodeRedemption).where(
                PromoCodeRedemption.promo_code_id == promo.id,
                PromoCodeRedemption.user_id == user_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="You have already redeemed this code")

        # Add credits to user balance
        credit_result = await db.execute(
            select(SearchCredit).where(SearchCredit.user_id == user_id)
        )
        credit = credit_result.scalar_one_or_none()

        if not credit:
            # get_or_create_credits uses its own session, so we create inline
            credit = SearchCredit(user_id=user_id, balance=3)
            db.add(credit)
            await db.flush()

        credit.balance += promo.credits
        new_balance = credit.balance

        # Record redemption
        db.add(PromoCodeRedemption(
            promo_code_id=promo.id,
            user_id=user_id,
            credits_granted=promo.credits,
        ))

        # Increment use count
        promo.use_count += 1

        await db.commit()
        return promo.credits, new_balance


async def get_admin_stats() -> dict:
    """Get platform statistics for admin dashboard."""
    async with async_session() as db:
        total_users = (await db.execute(
            select(func.count(User.id))
        )).scalar() or 0

        total_searches = (await db.execute(
            select(func.count(SearchHistory.id))
        )).scalar() or 0

        thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
        active_users_last_30d = (await db.execute(
            select(func.count(func.distinct(SearchHistory.user_id))).where(
                SearchHistory.created_at >= thirty_days_ago
            )
        )).scalar() or 0

        # Users with balance above the free cap (3)
        paid_users = (await db.execute(
            select(func.count(SearchCredit.id)).where(
                SearchCredit.balance > 3
            )
        )).scalar() or 0

        return {
            "total_users": total_users,
            "total_searches": total_searches,
            "active_users_last_30d": active_users_last_30d,
            "paid_users": paid_users,
        }
