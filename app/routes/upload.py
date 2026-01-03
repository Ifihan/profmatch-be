from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.services.redis import get_session, set_session
from app.utils.storage import save_file, validate_extension

router = APIRouter(prefix="/api/upload", tags=["upload"])

MAX_SIZE = settings.max_upload_size_mb * 1024 * 1024


class UploadResponse(BaseModel):
    """File upload response."""
    file_id: str
    filename: str


@router.post("", response_model=UploadResponse)
async def upload_file(
    session_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload CV or supporting document."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not validate_extension(file.filename):
        raise HTTPException(status_code=400, detail="File type not allowed. Use PDF, DOCX, or TXT")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Max {settings.max_upload_size_mb}MB")

    file_id = await save_file(session_id, file.filename, content)

    file_ids = session.get("file_ids", [])
    file_ids.append(file_id)
    session["file_ids"] = file_ids
    await set_session(session_id, session)

    return UploadResponse(file_id=file_id, filename=file.filename)
