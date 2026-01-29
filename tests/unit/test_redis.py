"""Unit tests for app/services/redis.py."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services import redis as redis_module


@pytest.fixture(autouse=True)
def reset_redis_state():
    """Reset redis module state before each test."""
    redis_module.redis_client = None
    redis_module.use_memory_store = False
    redis_module.memory_store.clear()
    yield
    redis_module.redis_client = None
    redis_module.use_memory_store = False
    redis_module.memory_store.clear()


class TestMemoryStoreFallback:
    """Tests for in-memory storage fallback when Redis is unavailable."""

    @pytest.mark.asyncio
    async def test_set_session_uses_memory_when_redis_unavailable(self):
        """When Redis is unavailable, session data is stored in memory."""
        redis_module.use_memory_store = True

        await redis_module.set_session("test-session", {"status": "created"})

        key = "session:test-session"
        assert key in redis_module.memory_store
        assert json.loads(redis_module.memory_store[key]) == {"status": "created"}

    @pytest.mark.asyncio
    async def test_get_session_from_memory(self):
        """Session data can be retrieved from memory store."""
        redis_module.use_memory_store = True
        redis_module.memory_store["session:test-session"] = json.dumps(
            {"status": "active", "file_ids": ["f1", "f2"]}
        )

        result = await redis_module.get_session("test-session")

        assert result == {"status": "active", "file_ids": ["f1", "f2"]}

    @pytest.mark.asyncio
    async def test_get_session_returns_none_for_missing(self):
        """Get session returns None for non-existent session."""
        redis_module.use_memory_store = True

        result = await redis_module.get_session("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_session_from_memory(self):
        """Session can be deleted from memory store."""
        redis_module.use_memory_store = True
        redis_module.memory_store["session:test-session"] = json.dumps({"status": "created"})

        result = await redis_module.delete_session("test-session")

        assert result is True
        assert "session:test-session" not in redis_module.memory_store

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session_returns_false(self):
        """Deleting non-existent session returns False."""
        redis_module.use_memory_store = True

        result = await redis_module.delete_session("nonexistent")

        assert result is False


class TestRedisConnection:
    """Tests for Redis connection handling."""

    @pytest.mark.asyncio
    async def test_get_redis_falls_back_to_memory_on_connection_error(self):
        """When Redis connection fails, fall back to memory store."""
        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping.side_effect = Exception("Connection refused")
            mock_from_url.return_value = mock_client

            result = await redis_module.get_redis()

            assert result is None
            assert redis_module.use_memory_store is True

    @pytest.mark.asyncio
    async def test_get_redis_returns_client_on_success(self):
        """When Redis connection succeeds, return the client."""
        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_from_url.return_value = mock_client

            result = await redis_module.get_redis()

            assert result == mock_client
            assert redis_module.use_memory_store is False

    @pytest.mark.asyncio
    async def test_get_redis_reuses_existing_client(self):
        """Existing Redis client is reused on subsequent calls."""
        mock_client = AsyncMock()
        redis_module.redis_client = mock_client

        result = await redis_module.get_redis()

        assert result == mock_client

    @pytest.mark.asyncio
    async def test_close_redis_closes_client(self):
        """close_redis properly closes the connection."""
        mock_client = AsyncMock()
        redis_module.redis_client = mock_client

        await redis_module.close_redis()

        mock_client.close.assert_called_once()
        assert redis_module.redis_client is None


class TestSessionOperationsWithRedis:
    """Tests for session operations with Redis client."""

    @pytest.mark.asyncio
    async def test_set_session_with_redis(self):
        """set_session uses Redis setex with TTL."""
        mock_client = AsyncMock()
        redis_module.redis_client = mock_client

        with patch.object(redis_module, "get_redis", return_value=mock_client):
            await redis_module.set_session("test-session", {"status": "created"})

        mock_client.setex.assert_called_once()
        call_args = mock_client.setex.call_args
        assert call_args[0][0] == "session:test-session"
        # TTL should be based on settings.session_ttl_hours * 3600
        assert call_args[0][2] == json.dumps({"status": "created"})

    @pytest.mark.asyncio
    async def test_get_session_with_redis(self):
        """get_session retrieves data from Redis."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=json.dumps({"status": "active"}))
        redis_module.redis_client = mock_client

        with patch.object(redis_module, "get_redis", return_value=mock_client):
            result = await redis_module.get_session("test-session")

        assert result == {"status": "active"}
        mock_client.get.assert_called_once_with("session:test-session")

    @pytest.mark.asyncio
    async def test_delete_session_with_redis(self):
        """delete_session removes data from Redis."""
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)
        redis_module.redis_client = mock_client

        with patch.object(redis_module, "get_redis", return_value=mock_client):
            result = await redis_module.delete_session("test-session")

        assert result is True
        mock_client.delete.assert_called_once_with("session:test-session")


class TestSessionDataIntegrity:
    """Tests for session data serialization/deserialization."""

    @pytest.mark.asyncio
    async def test_complex_session_data_roundtrip(self):
        """Complex session data survives JSON roundtrip."""
        redis_module.use_memory_store = True

        session_data = {
            "status": "matching",
            "university": "MIT",
            "research_interests": ["AI", "ML", "NLP"],
            "file_ids": ["file1", "file2"],
            "match_progress": 50,
            "current_step": "Enriching professors",
        }

        await redis_module.set_session("complex-session", session_data)
        result = await redis_module.get_session("complex-session")

        assert result == session_data

    @pytest.mark.asyncio
    async def test_session_with_nested_data(self):
        """Session with nested data structures works correctly."""
        redis_module.use_memory_store = True

        session_data = {
            "status": "completed",
            "match_results": [
                {"professor": "Dr. Smith", "score": 85.5},
                {"professor": "Dr. Jones", "score": 78.2},
            ],
        }

        await redis_module.set_session("nested-session", session_data)
        result = await redis_module.get_session("nested-session")

        assert result == session_data
        assert len(result["match_results"]) == 2
