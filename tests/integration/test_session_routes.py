"""Integration tests for session routes."""

import pytest
from unittest.mock import patch, AsyncMock

from app.services import redis as redis_module


@pytest.fixture(autouse=True)
def reset_redis_state():
    """Reset redis module state before each test."""
    redis_module.redis_client = None
    redis_module.use_memory_store = True  # Use memory store for tests
    redis_module.memory_store.clear()
    yield
    redis_module.redis_client = None
    redis_module.use_memory_store = False
    redis_module.memory_store.clear()


class TestCreateSession:
    """Tests for POST /api/session endpoint."""

    @pytest.mark.asyncio
    async def test_create_session_returns_session_id(self, test_app):
        """Creating a session returns a valid session ID."""
        response = await test_app.post("/api/session")

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_create_session_stores_initial_data(self, test_app):
        """Created session is stored with initial status."""
        response = await test_app.post("/api/session")
        session_id = response.json()["session_id"]

        # Verify session was stored
        key = f"session:{session_id}"
        assert key in redis_module.memory_store


class TestGetSession:
    """Tests for GET /api/session/{session_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_existing_session(self, test_app):
        """Getting an existing session returns its data."""
        # Create session first
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        # Get the session
        response = await test_app.get(f"/api/session/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["status"] == "created"

    @pytest.mark.asyncio
    async def test_get_nonexistent_session_returns_404(self, test_app):
        """Getting a non-existent session returns 404."""
        response = await test_app.get("/api/session/nonexistent-id")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestDeleteSession:
    """Tests for DELETE /api/session/{session_id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_existing_session(self, test_app):
        """Deleting an existing session succeeds."""
        # Create session first
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        # Mock the GCS delete to avoid actual calls
        with patch("app.routes.session.delete_session_files", new_callable=AsyncMock):
            response = await test_app.delete(f"/api/session/{session_id}")

        assert response.status_code == 200
        assert "deleted" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session_returns_404(self, test_app):
        """Deleting a non-existent session returns 404."""
        response = await test_app.delete("/api/session/nonexistent-id")

        assert response.status_code == 404


class TestSessionLifecycle:
    """Tests for complete session lifecycle."""

    @pytest.mark.asyncio
    async def test_full_session_lifecycle(self, test_app):
        """Test create -> get -> delete lifecycle."""
        # Create
        create_response = await test_app.post("/api/session")
        assert create_response.status_code == 200
        session_id = create_response.json()["session_id"]

        # Get
        get_response = await test_app.get(f"/api/session/{session_id}")
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "created"

        # Delete
        with patch("app.routes.session.delete_session_files", new_callable=AsyncMock):
            delete_response = await test_app.delete(f"/api/session/{session_id}")
        assert delete_response.status_code == 200

        # Verify deleted
        get_after_delete = await test_app.get(f"/api/session/{session_id}")
        assert get_after_delete.status_code == 404
