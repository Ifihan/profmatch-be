from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import User
from app.services import credits

router = APIRouter(prefix="/credits", tags=["credits"])


class BalanceResponse(BaseModel):
    balance: int


class PlansResponse(BaseModel):
    plans: list = []
    available: bool


@router.get("", response_model=BalanceResponse)
async def my_balance(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    balance = await credits.get_balance(db, user.id)
    await db.commit()  # persist any lazy-regen events surfaced during the read
    return {"balance": balance}


@router.get("/plans", response_model=PlansResponse)
async def credit_plans():
    """Purchasable credit packs — stub until Payments lands."""
    return {"plans": [], "available": False}
