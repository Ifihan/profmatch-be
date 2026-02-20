from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProfessorProfile
from app.models.database import ProfessorCache
from app.services.cache import cache_to_profile
from app.services.database import get_db

router = APIRouter(prefix="/api/professor", tags=["professor"])


@router.get("/{professor_id}", response_model=ProfessorProfile)
async def get_professor(professor_id: str, session: AsyncSession = Depends(get_db)):
    """Get detailed professor profile by ID."""
    stmt = select(ProfessorCache).where(ProfessorCache.id == professor_id)
    result = await session.execute(stmt)

    if not (cached := result.scalar_one_or_none()):
        raise HTTPException(status_code=404, detail="Professor not found")

    return cache_to_profile(cached)
