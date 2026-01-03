from fastapi import APIRouter, HTTPException

from app.models import ProfessorProfile
from app.services.cache import get_cached_professor_by_scholar_id
from app.services.database import async_session
from app.models.database import ProfessorCache
from sqlalchemy import select

router = APIRouter(prefix="/api/professor", tags=["professor"])


@router.get("/{professor_id}", response_model=ProfessorProfile)
async def get_professor(professor_id: str):
    """Get detailed professor profile by ID."""
    async with async_session() as session:
        stmt = select(ProfessorCache).where(ProfessorCache.id == professor_id)
        result = await session.execute(stmt)
        cached = result.scalar_one_or_none()

        if not cached:
            raise HTTPException(status_code=404, detail="Professor not found")

        from app.models import CitationMetrics, Publication

        publications = [Publication(**p) for p in cached.publications] if cached.publications else []
        citation_metrics = CitationMetrics(**cached.citation_metrics) if cached.citation_metrics else None

        return ProfessorProfile(
            id=cached.id,
            name=cached.name,
            university=cached.university,
            department=cached.department,
            title=cached.title,
            email=cached.email,
            scholar_id=cached.scholar_id,
            research_areas=cached.research_areas or [],
            publications=publications,
            citation_metrics=citation_metrics,
            last_updated=cached.updated_at,
        )
