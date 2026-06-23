"""Redis JSON cache for OpenAlex lookups. Fail-open: cache errors never break a search."""
import json
from typing import Any

from redis.asyncio import Redis
from app.core.config import settings

_redis: Redis | None = None


def _client() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def get_json(key: str) -> Any | None:
    try:
        raw = await _client().get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def set_json(key: str, value: Any, ttl: int) -> None:
    try:
        await _client().set(key, json.dumps(value), ex=ttl)
    except Exception:
        pass
