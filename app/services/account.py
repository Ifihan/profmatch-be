"""Account data removal: clearing search history and full account deletion."""
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import CreditEvent, MatchJob, JobStatus, PromoRedemption, User

_TERMINAL = (JobStatus.DONE, JobStatus.FAILED)


async def clear_history(db: AsyncSession, user_id: str) -> int:
    """Delete the user's finished searches; returns the number removed."""
    result = await db.execute(
        delete(MatchJob).where(MatchJob.user_id == user_id, MatchJob.status.in_(_TERMINAL))
    )
    await db.commit()
    return result.rowcount or 0


async def delete_account(db: AsyncSession, user_id: str) -> None:
    """Remove the user and all rows referencing them, in FK-safe order."""
    await db.execute(delete(CreditEvent).where(CreditEvent.user_id == user_id))
    await db.execute(delete(PromoRedemption).where(PromoRedemption.user_id == user_id))
    await db.execute(delete(MatchJob).where(MatchJob.user_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    await db.commit()
