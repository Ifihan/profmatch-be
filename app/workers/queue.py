"""ARQ queue wiring: API calls enqueue_match_job; the worker runs run_match_job (see worker.py)."""
from arq import create_pool
from arq.connections import RedisSettings
from app.core.config import settings

_pool = None


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


async def enqueue_match_job(job_id: str) -> None:
    pool = await get_pool()
    await pool.enqueue_job("run_match_job", job_id, _queue_name=settings.arq_queue_name)
