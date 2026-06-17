from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import User
from app.services import credits

router = APIRouter(prefix="/credits", tags=["credits"])


@router.get("")
async def my_balance(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    balance = await credits.get_balance(db, user.id)
    # Reading may materialise owed lazy-regen events — persist them.
    await db.commit()
    return {"balance": balance}


@router.get("/plans")
async def credit_plans():
    """Purchasable credit packs. Stub until Payments (Phase 5) lands; the ledger
    seam is already in place, so this will list real packs once wired."""
    return {"plans": [], "available": False}
