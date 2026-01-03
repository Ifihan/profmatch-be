from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import ProfessorProfile, CitationMetrics, Publication
from app.models.database import ProfessorCache
from app.services.database import async_session

CACHE_TTL_DAYS = 7


async def get_cached_professor(name: str, university: str) -> ProfessorProfile | None:
    """Get professor from cache if fresh."""
    async with async_session() as session:
        stmt = select(ProfessorCache).where(
            ProfessorCache.name == name,
            ProfessorCache.university == university,
            ProfessorCache.updated_at > datetime.utcnow() - timedelta(days=CACHE_TTL_DAYS),
        )
        result = await session.execute(stmt)
        cached = result.scalar_one_or_none()

        if not cached:
            return None

        return _cache_to_profile(cached)


async def get_cached_professor_by_scholar_id(scholar_id: str) -> ProfessorProfile | None:
    """Get professor from cache by Scholar ID."""
    async with async_session() as session:
        stmt = select(ProfessorCache).where(
            ProfessorCache.scholar_id == scholar_id,
            ProfessorCache.updated_at > datetime.utcnow() - timedelta(days=CACHE_TTL_DAYS),
        )
        result = await session.execute(stmt)
        cached = result.scalar_one_or_none()

        if not cached:
            return None

        return _cache_to_profile(cached)


async def cache_professor(profile: ProfessorProfile) -> None:
    """Store professor in cache."""
    async with async_session() as session:
        stmt = select(ProfessorCache).where(
            ProfessorCache.name == profile.name,
            ProfessorCache.university == profile.university,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.department = profile.department
            existing.title = profile.title
            existing.email = profile.email
            existing.scholar_id = profile.scholar_id
            existing.research_areas = profile.research_areas
            existing.publications = [p.model_dump() for p in profile.publications]
            existing.citation_metrics = profile.citation_metrics.model_dump() if profile.citation_metrics else None
            existing.updated_at = datetime.utcnow()
        else:
            cached = ProfessorCache(
                id=str(profile.id),
                name=profile.name,
                university=profile.university,
                department=profile.department,
                title=profile.title,
                email=profile.email,
                scholar_id=profile.scholar_id,
                research_areas=profile.research_areas,
                publications=[p.model_dump() for p in profile.publications],
                citation_metrics=profile.citation_metrics.model_dump() if profile.citation_metrics else None,
            )
            session.add(cached)

        await session.commit()


async def get_cached_professors_by_university(university: str) -> list[ProfessorProfile]:
    """Get all cached professors for a university."""
    async with async_session() as session:
        stmt = select(ProfessorCache).where(
            ProfessorCache.university == university,
            ProfessorCache.updated_at > datetime.utcnow() - timedelta(days=CACHE_TTL_DAYS),
        )
        result = await session.execute(stmt)
        cached_list = result.scalars().all()

        return [_cache_to_profile(c) for c in cached_list]


def _cache_to_profile(cached: ProfessorCache) -> ProfessorProfile:
    """Convert cache record to ProfessorProfile."""
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
