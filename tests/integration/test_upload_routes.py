"""Integration tests for upload routes."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from io import BytesIO

from app.services import redis as redis_module


@pytest.fixture(autouse=True)
def reset_redis_state():
    """Reset redis module state before each test."""
    redis_module.redis_client = None
    redis_module.use_memory_store = True
    redis_module.memory_store.clear()
    yield
    redis_module.redis_client = None
    redis_module.use_memory_store = False
    redis_module.memory_store.clear()


@pytest.fixture
def mock_gcs():
    """Mock GCS operations."""
    with patch("app.utils.storage.get_gcs_bucket") as mock_bucket:
        mock_blob = MagicMock()
        mock_bucket.return_value.blob.return_value = mock_blob
        yield mock_bucket


class TestUploadFile:
    """Tests for POST /api/upload endpoint."""

    @pytest.mark.asyncio
    async def test_upload_pdf_success(self, test_app, mock_gcs):
        """Uploading a PDF file succeeds."""
        # Create session first
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        # Upload file
        files = {"file": ("resume.pdf", b"PDF content", "application/pdf")}
        data = {"session_id": session_id}

        response = await test_app.post("/api/upload", files=files, data=data)

        assert response.status_code == 200
        result = response.json()
        assert "file_id" in result
        assert result["filename"] == "resume.pdf"

    @pytest.mark.asyncio
    async def test_upload_docx_success(self, test_app, mock_gcs):
        """Uploading a DOCX file succeeds."""
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        files = {"file": ("resume.docx", b"DOCX content", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        data = {"session_id": session_id}

        response = await test_app.post("/api/upload", files=files, data=data)

        assert response.status_code == 200
        assert response.json()["filename"] == "resume.docx"

    @pytest.mark.asyncio
    async def test_upload_txt_success(self, test_app, mock_gcs):
        """Uploading a TXT file succeeds."""
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        files = {"file": ("resume.txt", b"Plain text content", "text/plain")}
        data = {"session_id": session_id}

        response = await test_app.post("/api/upload", files=files, data=data)

        assert response.status_code == 200
        assert response.json()["filename"] == "resume.txt"

    @pytest.mark.asyncio
    async def test_upload_invalid_extension_rejected(self, test_app):
        """Uploading file with invalid extension returns 400."""
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        files = {"file": ("script.exe", b"malicious content", "application/octet-stream")}
        data = {"session_id": session_id}

        response = await test_app.post("/api/upload", files=files, data=data)

        assert response.status_code == 400
        assert "not allowed" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upload_to_nonexistent_session_fails(self, test_app, mock_gcs):
        """Uploading to non-existent session returns 404."""
        files = {"file": ("resume.pdf", b"PDF content", "application/pdf")}
        data = {"session_id": "nonexistent-session"}

        response = await test_app.post("/api/upload", files=files, data=data)

        assert response.status_code == 404
        assert "session not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upload_updates_session_file_ids(self, test_app, mock_gcs):
        """Upload adds file_id to session data."""
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        files = {"file": ("resume.pdf", b"PDF content", "application/pdf")}
        data = {"session_id": session_id}

        response = await test_app.post("/api/upload", files=files, data=data)
        file_id = response.json()["file_id"]

        # Verify session was updated
        get_response = await test_app.get(f"/api/session/{session_id}")
        session_data = get_response.json()
        assert file_id in session_data["file_ids"]

    @pytest.mark.asyncio
    async def test_upload_multiple_files(self, test_app, mock_gcs):
        """Multiple files can be uploaded to same session."""
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        # Upload first file
        files1 = {"file": ("resume.pdf", b"PDF content", "application/pdf")}
        response1 = await test_app.post("/api/upload", files=files1, data={"session_id": session_id})
        file_id1 = response1.json()["file_id"]

        # Upload second file
        files2 = {"file": ("cover_letter.docx", b"DOCX content", "application/docx")}
        response2 = await test_app.post("/api/upload", files=files2, data={"session_id": session_id})
        file_id2 = response2.json()["file_id"]

        # Verify both files in session
        get_response = await test_app.get(f"/api/session/{session_id}")
        session_data = get_response.json()
        assert file_id1 in session_data["file_ids"]
        assert file_id2 in session_data["file_ids"]
        assert len(session_data["file_ids"]) == 2


class TestUploadValidation:
    """Tests for upload validation."""

    @pytest.mark.asyncio
    async def test_jpg_rejected(self, test_app):
        """Image files are rejected."""
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        files = {"file": ("photo.jpg", b"image data", "image/jpeg")}
        data = {"session_id": session_id}

        response = await test_app.post("/api/upload", files=files, data=data)

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_py_rejected(self, test_app):
        """Python files are rejected."""
        create_response = await test_app.post("/api/session")
        session_id = create_response.json()["session_id"]

        files = {"file": ("script.py", b"print('hello')", "text/x-python")}
        data = {"session_id": session_id}

        response = await test_app.post("/api/upload", files=files, data=data)

        assert response.status_code == 400
