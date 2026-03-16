import json
import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.database import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=settings.debug)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create database tables."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info(json.dumps({"event": "database_initialized"}))
    except Exception as e:
        logger.warning(json.dumps({"event": "database_init_skipped", "error": str(e)}))


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Get database session."""
    async with async_session() as session:
        yield session


async def close_db() -> None:
    """Close database engine."""
    await engine.dispose()