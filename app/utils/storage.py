import asyncio
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from google.cloud import storage

from app.config import settings

UPLOAD_DIR = Path("uploads")
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}

# Initialize GCS client
_gcs_client = None


def get_gcs_client() -> storage.Client:
    """Get or create GCS client."""
    global _gcs_client
    if _gcs_client is None:
        _gcs_client = storage.Client(project=settings.gcs_project_id)
    return _gcs_client


def get_gcs_bucket() -> storage.Bucket:
    """Get GCS bucket."""
    client = get_gcs_client()
    return client.bucket(settings.gcs_bucket_name)


def ensure_upload_dir() -> None:
    """Create upload directory if it doesn't exist."""
    UPLOAD_DIR.mkdir(exist_ok=True)


def validate_extension(filename: str) -> bool:
    """Check if file extension is allowed."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


async def save_file(session_id: str, filename: str, content: bytes) -> str:
    """Save uploaded file to GCS and return file_id."""
    file_id = str(uuid4())
    ext = Path(filename).suffix.lower()

    # Create blob path: session_id/file_id.ext
    blob_name = f"{session_id}/{file_id}{ext}"

    def _upload():
        bucket = get_gcs_bucket()
        blob = bucket.blob(blob_name)

        # Add metadata for tracking
        blob.metadata = {
            "session_id": session_id,
            "original_filename": filename,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }

        # Upload to GCS
        blob.upload_from_string(content)

    # Run blocking I/O in thread pool to avoid blocking event loop
    await asyncio.to_thread(_upload)

    return file_id


async def get_file_path(session_id: str, file_id: str) -> Path | None:
    """Download file from GCS to temporary location and return path."""
    def _download():
        bucket = get_gcs_bucket()

        for ext in ALLOWED_EXTENSIONS:
            blob_name = f"{session_id}/{file_id}{ext}"
            blob = bucket.blob(blob_name)
            if blob.exists():
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                blob.download_to_filename(temp_file.name)
                return Path(temp_file.name)

        return None

    # Run blocking I/O in thread pool to avoid blocking event loop
    return await asyncio.to_thread(_download)


async def delete_session_files(session_id: str) -> None:
    """Delete all files for a session from GCS using batch delete."""
    def _delete():
        bucket = get_gcs_bucket()

        # Collect all blobs for the session
        blobs = list(bucket.list_blobs(prefix=f"{session_id}/"))

        if blobs:
            bucket.delete_blobs(blobs)

    # Run blocking I/O in thread pool to avoid blocking event loop
    await asyncio.to_thread(_delete)


async def cleanup_old_sessions(hours: int = 24) -> int:
    """
    Delete session folders older than specified hours.
    Returns the number of sessions cleaned up.

    Optimized to collect blobs for deletion in a single pass and batch delete.
    """
    def _cleanup():
        bucket = get_gcs_bucket()
        cutoff_time = datetime.now(timezone.utc).timestamp() - (hours * 3600)
        sessions_to_delete: set[str] = set()
        blobs_to_delete: list[storage.Blob] = []

        for blob in bucket.list_blobs():
            if blob.time_created.timestamp() < cutoff_time:
                session_id = blob.name.split("/")[0]
                sessions_to_delete.add(session_id)
                blobs_to_delete.append(blob)

        # Batch delete all old blobs at once
        if blobs_to_delete:
            bucket.delete_blobs(blobs_to_delete)

        return len(sessions_to_delete)

    # Run blocking I/O in thread pool to avoid blocking event loop
    return await asyncio.to_thread(_cleanup)
