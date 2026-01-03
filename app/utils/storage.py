import os
import shutil
from pathlib import Path
from uuid import uuid4

UPLOAD_DIR = Path("uploads")
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def ensure_upload_dir() -> None:
    """Create upload directory if it doesn't exist."""
    UPLOAD_DIR.mkdir(exist_ok=True)


def validate_extension(filename: str) -> bool:
    """Check if file extension is allowed."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


async def save_file(session_id: str, filename: str, content: bytes) -> str:
    """Save uploaded file and return file_id."""
    ensure_upload_dir()
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(exist_ok=True)

    file_id = str(uuid4())
    ext = Path(filename).suffix.lower()
    file_path = session_dir / f"{file_id}{ext}"

    with open(file_path, "wb") as f:
        f.write(content)

    return file_id


def get_file_path(session_id: str, file_id: str) -> Path | None:
    """Get path to uploaded file."""
    session_dir = UPLOAD_DIR / session_id
    if not session_dir.exists():
        return None

    for file in session_dir.iterdir():
        if file.stem == file_id:
            return file
    return None


def delete_session_files(session_id: str) -> None:
    """Delete all files for a session."""
    session_dir = UPLOAD_DIR / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)
