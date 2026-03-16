"""Background cleanup service for removing old session files from GCS and expired DB sessions."""

import asyncio
import json
import logging
import time

from app.config import settings
from app.services.session_store import delete_expired_sessions
from app.utils.storage import cleanup_old_sessions

logger = logging.getLogger(__name__)

_cleanup_task = None


async def cleanup_loop():
    """Background task that runs cleanup every hour."""
    while True:
        await asyncio.sleep(3600)
        wide_event = {"event": "cleanup_run", "start_time": time.time()}
        try:
            wide_event["gcs_sessions_cleaned"] = await cleanup_old_sessions(
                hours=settings.session_ttl_hours
            )
            wide_event["db_sessions_expired"] = await delete_expired_sessions()
            wide_event["outcome"] = "success"
        except Exception as e:
            wide_event["outcome"] = "error"
            wide_event["error"] = {"type": type(e).__name__, "message": str(e)}
        finally:
            wide_event["duration_ms"] = int(
                (time.time() - wide_event["start_time"]) * 1000
            )
            logger.info(json.dumps(wide_event, default=str))


async def start_cleanup_task():
    """Start the background cleanup task."""
    global _cleanup_task
    if _cleanup_task is None:
        _cleanup_task = asyncio.create_task(cleanup_loop())
        logger.info(json.dumps({"event": "cleanup_task_started"}))


async def stop_cleanup_task():
    """Stop the background cleanup task."""
    global _cleanup_task
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None
        logger.info(json.dumps({"event": "cleanup_task_stopped"}))
