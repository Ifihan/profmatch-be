"""Search credits service: balance calculation, deduction, replenishment."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.database import AnonymousUsage, SearchCredit, SearchUsage
from app.services.database import async_session

REPLENISH_HOURS = 72  # 1 free credit every 3 days
MAX_FREE_CREDITS = 3
MAX_ANONYMOUS_SEARCHES = 1


async def get_or_create_credits(user_id: str) -> SearchCredit:
    """Get credit record for a user, creating one with 3 credits if missing."""
    async with async_session() as db:
        result = await db.execute(
            select(SearchCredit).where(SearchCredit.user_id == user_id)
        )
        credit = result.scalar_one_or_none()

        if not credit:
            credit = SearchCredit(user_id=user_id, balance=3)
            db.add(credit)
            await db.commit()
            await db.refresh(credit)

        return credit


def calculate_available_credits(credit: SearchCredit) -> int:
    """Calculate balance including any earned free credits."""
    if credit.balance >= MAX_FREE_CREDITS:
        return credit.balance  # Already at or above free cap

    hours_since_last = (datetime.now(UTC) - credit.last_free_credit_at).total_seconds() / 3600
    earned = int(hours_since_last // REPLENISH_HOURS)
    return min(credit.balance + earned, MAX_FREE_CREDITS)


def next_free_credit_at(credit: SearchCredit) -> datetime | None:
    """Return when the next free credit will be available, or None if balance >= 3."""
    effective_balance = calculate_available_credits(credit)
    if effective_balance >= MAX_FREE_CREDITS:
        return None
    hours_since_last = (datetime.now(UTC) - credit.last_free_credit_at).total_seconds() / 3600
    earned = int(hours_since_last // REPLENISH_HOURS)
    return credit.last_free_credit_at + timedelta(hours=(earned + 1) * REPLENISH_HOURS)


async def deduct_credit(*, user_id: str, match_id: str, university: str) -> bool:
    """Deduct 1 credit. Returns True if successful, False if insufficient balance."""
    async with async_session() as db:
        result = await db.execute(
            select(SearchCredit).where(SearchCredit.user_id == user_id)
        )
        credit = result.scalar_one_or_none()

        if not credit:
            # Auto-create with 3 credits
            credit = SearchCredit(user_id=user_id, balance=3)
            db.add(credit)
            await db.flush()

        # Apply any earned free credits before deducting
        effective_balance = calculate_available_credits(credit)
        if effective_balance <= 0:
            return False

        # Persist the earned credits + deduction
        credit.balance = effective_balance - 1
        credit.last_free_credit_at = datetime.now(UTC)

        # Log usage
        db.add(SearchUsage(
            user_id=user_id,
            match_id=match_id,
            university=university,
        ))

        await db.commit()
        return True


async def check_anonymous_limit(ip: str) -> bool:
    """Return True if the IP has anonymous searches remaining."""
    async with async_session() as db:
        result = await db.execute(
            select(AnonymousUsage).where(AnonymousUsage.ip_address == ip)
        )
        usage = result.scalar_one_or_none()
        if not usage:
            return True
        return usage.search_count < MAX_ANONYMOUS_SEARCHES


async def record_anonymous_search(ip: str) -> None:
    """Increment anonymous search count for an IP (upsert)."""
    async with async_session() as db:
        result = await db.execute(
            select(AnonymousUsage).where(AnonymousUsage.ip_address == ip)
        )
        usage = result.scalar_one_or_none()

        if usage:
            usage.search_count += 1
        else:
            db.add(AnonymousUsage(ip_address=ip, search_count=1))

        await db.commit()


async def get_usage_history(user_id: str, *, limit: int = 20) -> list[SearchUsage]:
    """Get recent search usage for a user."""
    async with async_session() as db:
        result = await db.execute(
            select(SearchUsage)
            .where(SearchUsage.user_id == user_id)
            .order_by(SearchUsage.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
