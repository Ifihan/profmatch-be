"""Credit ledger: balance is SUM of an append-only event log; regen materialised lazily on read."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models import CreditEvent, CreditEventType


async def _lock_user(db: AsyncSession, user_id: str) -> None:
    """Serialize a user's credit mutations via a transaction-scoped advisory lock; safe to nest."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"credit:{user_id}"}
    )


async def _raw_balance(db: AsyncSession, user_id: str) -> int:
    stmt = select(func.coalesce(func.sum(CreditEvent.delta), 0)).where(
        CreditEvent.user_id == user_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def _last_event_at(db: AsyncSession, user_id: str) -> datetime | None:
    stmt = select(func.max(CreditEvent.created_at)).where(CreditEvent.user_id == user_id)
    return (await db.execute(stmt)).scalar_one()


async def _apply_lazy_regen(db: AsyncSession, user_id: str) -> None:
    """Materialise owed regen events (capped at max) when below max and time has passed."""
    await _lock_user(db, user_id)
    balance = await _raw_balance(db, user_id)
    if balance >= settings.registered_max_credits:
        return
    last = await _last_event_at(db, user_id)
    if last is None:
        return
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    interval = timedelta(hours=settings.credit_regen_interval_hours)
    now = datetime.now(timezone.utc)
    earned = int((now - last) // interval)
    if earned <= 0:
        return

    grantable = min(earned, settings.registered_max_credits - balance)
    for i in range(grantable):
        db.add(CreditEvent(
            user_id=user_id,
            event_type=CreditEventType.GRANT_REGEN,
            delta=1,
            # backdate so the regen clock stays consistent
            created_at=last + interval * (i + 1),
        ))
    await db.flush()


async def get_balance(db: AsyncSession, user_id: str) -> int:
    await _apply_lazy_regen(db, user_id)
    return await _raw_balance(db, user_id)


async def grant(
    db: AsyncSession, user_id: str, amount: int,
    event_type: CreditEventType, reference: str | None = None,
) -> None:
    db.add(CreditEvent(
        user_id=user_id, event_type=event_type, delta=abs(amount), reference=reference
    ))
    await db.flush()


async def try_spend(db: AsyncSession, user_id: str, reference: str) -> bool:
    """Spend one credit atomically (False if empty); shares the job-creating transaction."""
    await _lock_user(db, user_id)
    balance = await get_balance(db, user_id)
    if balance < 1:
        return False
    db.add(CreditEvent(
        user_id=user_id,
        event_type=CreditEventType.SPEND_SEARCH,
        delta=-1,
        reference=reference,
    ))
    await db.flush()
    return True


async def refund(db: AsyncSession, user_id: str, reference: str) -> None:
    await grant(db, user_id, 1, CreditEventType.GRANT_REFUND, reference)
