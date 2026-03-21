from fastapi import APIRouter

from app.dependencies import CurrentAdmin
from app.models import (
    AdminStatsResponse,
    CreatePromoCodeRequest,
    MessageResponse,
    PromoCodeListResponse,
    PromoCodeResponse,
    TogglePromoCodeRequest,
)
from app.services import promo

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats", response_model=AdminStatsResponse)
async def get_stats(admin: CurrentAdmin):
    """Get platform statistics."""
    stats = await promo.get_admin_stats()
    return AdminStatsResponse(**stats)


@router.post("/promo-codes", response_model=PromoCodeResponse, status_code=201)
async def create_promo_code(body: CreatePromoCodeRequest, admin: CurrentAdmin):
    """Create a new promo code."""
    code = await promo.create_promo_code(
        code=body.code,
        credits=body.credits,
        max_uses=body.max_uses,
        created_by=admin.id,
    )
    return PromoCodeResponse(
        id=code.id,
        code=code.code,
        credits=code.credits,
        max_uses=code.max_uses,
        use_count=code.use_count,
        is_active=code.is_active,
        created_at=code.created_at,
    )


@router.get("/promo-codes", response_model=PromoCodeListResponse)
async def list_promo_codes(admin: CurrentAdmin):
    """List all promo codes."""
    codes = await promo.list_promo_codes()
    return PromoCodeListResponse(
        promo_codes=[
            PromoCodeResponse(
                id=c.id,
                code=c.code,
                credits=c.credits,
                max_uses=c.max_uses,
                use_count=c.use_count,
                is_active=c.is_active,
                created_at=c.created_at,
            )
            for c in codes
        ]
    )


@router.patch("/promo-codes/{promo_id}", response_model=PromoCodeResponse)
async def toggle_promo_code(
    promo_id: str, body: TogglePromoCodeRequest, admin: CurrentAdmin
):
    """Enable or disable a promo code."""
    code = await promo.toggle_promo_code(promo_id=promo_id, is_active=body.is_active)
    return PromoCodeResponse(
        id=code.id,
        code=code.code,
        credits=code.credits,
        max_uses=code.max_uses,
        use_count=code.use_count,
        is_active=code.is_active,
        created_at=code.created_at,
    )


@router.delete("/promo-codes/{promo_id}", response_model=MessageResponse)
async def delete_promo_code(promo_id: str, admin: CurrentAdmin):
    """Delete a promo code."""
    await promo.delete_promo_code(promo_id=promo_id)
    return MessageResponse(message="Promo code deleted")
