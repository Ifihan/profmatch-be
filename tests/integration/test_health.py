"""Integration tests for health check endpoint."""

import pytest


class TestHealthCheck:
    """Tests for health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, test_app):
        """Health endpoint returns 200."""
        response = await test_app.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_root_redirects_to_docs(self, test_app):
        """Root endpoint redirects to docs."""
        response = await test_app.get("/", follow_redirects=False)

        # Should redirect to /docs
        assert response.status_code == 307
        assert "/docs" in response.headers.get("location", "")
