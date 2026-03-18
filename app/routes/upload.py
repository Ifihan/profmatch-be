import logging
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from app.config import settings
from app.models import UploadResponse
from app.services import gemini, tools
from app.services.session_store import get_session, set_session, update_session_fields
from app.utils.storage import save_file, validate_extension

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upload", tags=["upload"])

MAX_SIZE = settings.max_upload_size_mb * 1024 * 1024


@router.post("", response_model=UploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    session_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload CV or supporting document."""
    if not (session := await get_session(session_id=session_id)):
        raise HTTPException(status_code=404, detail="Session not found")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not validate_extension(file.filename):
        raise HTTPException(status_code=400, detail="File type not allowed. Use PDF, DOCX, or TXT")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Max {settings.max_upload_size_mb}MB")

    # Generate file_id upfront so we can return immediately
    file_id = str(uuid4())

    file_ids = session.get("file_ids", [])
    file_ids.append(file_id)
    session["file_ids"] = file_ids
    await set_session(session_id=session_id, data=session)

    # Upload to GCS and pre-parse CV in background — doesn't block the response
    background_tasks.add_task(save_file, session_id, file.filename, content, file_id)
    background_tasks.add_task(
        _parse_and_cache_cv, session_id, file_id, file.filename, content
    )

    return UploadResponse(file_id=file_id, filename=file.filename)


async def _parse_and_cache_cv(
    session_id: str, file_id: str, filename: str, content: bytes
) -> None:
    """Parse CV in background and store result in session for later use."""
    try:
        ext = Path(filename).suffix.lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        text = tools.extract_text_from_file(file_path=tmp_path)
        Path(tmp_path).unlink(missing_ok=True)

        parsed = await gemini.parse_cv(text=text)

        await update_session_fields(
            session_id=session_id,
            updates={
                "parsed_cvs": {
                    **(await get_session(session_id=session_id) or {}).get("parsed_cvs", {}),
                    file_id: parsed.model_dump(),
                },
            },
        )
    except Exception:
        logger.exception("Background CV parse failed for file_id=%s", file_id)
