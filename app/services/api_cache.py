"""Redis-based API response caching.

Caches external API responses (web search results, faculty pages,
Google Scholar URLs/metrics, Semantic Scholar searches) to avoid
redundant calls across matching requests.
"""

import hashlib
import json
import logging

from app.services.redis import get_redis

logger = logging.getLogger(__name__)


def _cache_key(prefix: str, *parts: str) -> str:
    """Build a cache key from prefix and parts."""
    raw = ":".join(parts)
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"cache:{prefix}:{h}"


async def get_cached(prefix: str, *parts: str) -> dict | list | None:
    """Get a cached API response. Returns None on miss."""
    key = _cache_key(prefix, *parts)
    try:
        client = await get_redis()
        data = await client.get(key)
        if data is None:
            return None
        return json.loads(data)
    except Exception as e:
        logger.debug(f"Cache read error for {key}: {e}")
        return None


async def set_cached(
    prefix: str, *parts: str, data: dict | list, ttl_days: int = 7
) -> None:
    """Cache an API response with TTL in days."""
    key = _cache_key(prefix, *parts)
    ttl_seconds = ttl_days * 86400
    try:
        client = await get_redis()
        await client.setex(key, ttl_seconds, json.dumps(data, default=str))
    except Exception as e:
        logger.debug(f"Cache write error for {key}: {e}")
