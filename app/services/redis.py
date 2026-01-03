import json
import logging
from typing import Any

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

redis_client: redis.Redis | None = None
use_memory_store = False
memory_store: dict[str, str] = {}


async def get_redis() -> redis.Redis | None:
    """Get Redis client instance, or None if unavailable."""
    global redis_client, use_memory_store

    if use_memory_store:
        return None

    if redis_client is None:
        try:
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            await redis_client.ping()
            logger.info("Connected to Redis")
        except Exception as e:
            logger.warning(f"Redis unavailable, using in-memory store: {e}")
            use_memory_store = True
            redis_client = None
            return None

    return redis_client


async def close_redis() -> None:
    """Close Redis connection."""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None


async def set_session(session_id: str, data: dict[str, Any]) -> None:
    """Store session data."""
    client = await get_redis()
    key = f"session:{session_id}"

    if client:
        ttl_seconds = settings.session_ttl_hours * 3600
        await client.setex(key, ttl_seconds, json.dumps(data))
    else:
        memory_store[key] = json.dumps(data)


async def get_session(session_id: str) -> dict[str, Any] | None:
    """Retrieve session data."""
    client = await get_redis()
    key = f"session:{session_id}"

    if client:
        data = await client.get(key)
    else:
        data = memory_store.get(key)

    return json.loads(data) if data else None


async def delete_session(session_id: str) -> bool:
    """Delete session data."""
    client = await get_redis()
    key = f"session:{session_id}"

    if client:
        result = await client.delete(key)
        return result > 0
    else:
        if key in memory_store:
            del memory_store[key]
            return True
        return False