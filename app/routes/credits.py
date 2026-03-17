"""Search credits routes: balance, usage history, plans."""

from app.dependencies import CurrentUser
from app.models.schemas import CreditsResponse, PlanInfo, PlansResponse, SearchUsageItem
from app.services.credits import (
    calculate_available_credits,
    get_or_create_credits,
    get_usage_history,
    next_free_credit_at,
)

from fastapi import APIRouter

router = APIRouter(prefix="/api/credits", tags=["credits"])

PLANS = [
    PlanInfo(id="starter", name="Starter", credits=15, price_usd=5.00),
    PlanInfo(id="explorer", name="Explorer", credits=40, price_usd=12.00),
    PlanInfo(id="researcher", name="Researcher", credits=100, price_usd=25.00),
]


@router.get("", response_model=CreditsResponse)
async def get_credits(user: CurrentUser):
    """Get current credit balance and usage history."""
    credit = await get_or_create_credits(user.id)
    usage = await get_usage_history(user.id)

    return CreditsResponse(
        balance=calculate_available_credits(credit),
        next_free_credit_at=next_free_credit_at(credit),
        usage_history=[
            SearchUsageItem(
                match_id=u.match_id,
                university=u.university,
                created_at=u.created_at,
            )
            for u in usage
        ],
    )


@router.get("/plans", response_model=PlansResponse)
async def get_plans():
    """Get available credit purchase plans."""
    return PlansResponse(plans=PLANS)
