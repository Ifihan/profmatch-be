from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select, tuple_

from app.models import CitationMetrics, ProfessorProfile, Publication
from app.models.database import FacultyCache, ProfessorCache
from app.services.database import async_session

CACHE_TTL_DAYS = 7


async def get_cached_professor(*, name: str, university: str) -> ProfessorProfile | None:
    """Get professor from cache if fresh."""
    async with async_session() as session:
        stmt = select(ProfessorCache).where(
            ProfessorCache.name == name,
            ProfessorCache.university == university,
            ProfessorCache.updated_at > datetime.now(UTC) - timedelta(days=CACHE_TTL_DAYS),
        )
        result = await session.execute(stmt)
        cached = result.scalar_one_or_none()

        if not cached:
            return None

        return cache_to_profile(cached)


async def get_cached_professor_by_openalex_id(*, openalex_id: str) -> ProfessorProfile | None:
    """Get professor from cache by OpenAlex ID."""
    async with async_session() as session:
        stmt = select(ProfessorCache).where(
            ProfessorCache.openalex_id == openalex_id,
            ProfessorCache.updated_at > datetime.now(UTC) - timedelta(days=CACHE_TTL_DAYS),
        )
        result = await session.execute(stmt)
        if cached := result.scalar_one_or_none():
            return cache_to_profile(cached)
        return None



async def get_cached_professors_batch(
    *,
    lookups: list[tuple[str | None, str, str]],
) -> dict[str, ProfessorProfile]:
    """Batch-fetch cached professors in a single DB query.

    Args:
        lookups: list of (openalex_id, name, university) tuples.

    Returns:
        dict keyed by openalex_id or "name|university" -> ProfessorProfile.
    """
    if not lookups:
        return {}

    cutoff = datetime.now(UTC) - timedelta(days=CACHE_TTL_DAYS)

    openalex_ids = [oa_id for oa_id, _, _ in lookups if oa_id]
    name_uni_pairs = [(name, uni) for _, name, uni in lookups if name]

    conditions = []
    if openalex_ids:
        conditions.append(ProfessorCache.openalex_id.in_(openalex_ids))
    if name_uni_pairs:
        conditions.append(
            tuple_(ProfessorCache.name, ProfessorCache.university).in_(name_uni_pairs)
        )

    if not conditions:
        return {}

    async with async_session() as session:
        stmt = select(ProfessorCache).where(
            or_(*conditions),
            ProfessorCache.updated_at > cutoff,
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    found: dict[str, ProfessorProfile] = {}
    for row in rows:
        profile = cache_to_profile(row)
        if row.openalex_id:
            found[row.openalex_id] = profile
        found[f"{row.name}|{row.university}"] = profile

    return found


async def cache_professor(*, profile: ProfessorProfile) -> None:
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
            existing.openalex_id = profile.openalex_id
            existing.google_scholar_url = profile.google_scholar_url
            existing.directory_url = profile.directory_url
            existing.website = profile.website
            existing.research_areas = profile.research_areas
            existing.publications = [p.model_dump() for p in profile.publications]
            existing.citation_metrics = profile.citation_metrics.model_dump() if profile.citation_metrics else None
            existing.updated_at = datetime.now(UTC)
        else:
            cached = ProfessorCache(
                id=str(profile.id),
                name=profile.name,
                university=profile.university,
                department=profile.department,
                title=profile.title,
                email=profile.email,
                openalex_id=profile.openalex_id,
                google_scholar_url=profile.google_scholar_url,
                directory_url=profile.directory_url,
                website=profile.website,
                research_areas=profile.research_areas,
                publications=[p.model_dump() for p in profile.publications],
                citation_metrics=profile.citation_metrics.model_dump() if profile.citation_metrics else None,
            )
            session.add(cached)

        await session.commit()


async def get_cached_professors_by_university(*, university: str) -> list[ProfessorProfile]:
    """Get all cached professors for a university."""
    async with async_session() as session:
        stmt = select(ProfessorCache).where(
            ProfessorCache.university == university,
            ProfessorCache.updated_at > datetime.now(UTC) - timedelta(days=CACHE_TTL_DAYS),
        )
        result = await session.execute(stmt)
        cached_list = result.scalars().all()

        return [cache_to_profile(c) for c in cached_list]


# --- Faculty cache ---


async def get_cached_faculty(*, source_url: str) -> list[dict[str, Any]] | None:
    """Get cached faculty members for a source URL. Returns None on cache miss."""
    async with async_session() as session:
        stmt = select(FacultyCache).where(
            FacultyCache.source_url == source_url,
            FacultyCache.updated_at > datetime.now(UTC) - timedelta(days=CACHE_TTL_DAYS),
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        if not rows:
            return None
        return [
            {
                "name": r.name,
                "title": r.title,
                "department": r.department,
                "email": r.email,
                "profile_url": r.profile_url,
            }
            for r in rows
        ]


async def cache_faculty(
    *, source_url: str, university: str, members: list[dict[str, Any]]
) -> None:
    """Store faculty members for a source URL (replace strategy)."""
    async with async_session() as session:
        stmt = delete(FacultyCache).where(FacultyCache.source_url == source_url)
        await session.execute(stmt)

        for m in members:
            session.add(FacultyCache(
                name=m.get("name", ""),
                title=m.get("title"),
                department=m.get("department"),
                email=m.get("email"),
                profile_url=m.get("profile_url"),
                source_url=source_url,
                university=university,
            ))
        await session.commit()



def cache_to_profile(cached: ProfessorCache) -> ProfessorProfile:
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
        openalex_id=cached.openalex_id,
        google_scholar_url=cached.google_scholar_url,
        directory_url=cached.directory_url,
        website=cached.website,
        research_areas=cached.research_areas or [],
        publications=publications,
        citation_metrics=citation_metrics,
        last_updated=cached.updated_at,
    )
