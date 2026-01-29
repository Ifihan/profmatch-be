"""Background cleanup service for removing old session files from GCS."""

import asyncio
import logging

from app.config import settings
from app.utils.storage import cleanup_old_sessions

logger = logging.getLogger(__name__)

_cleanup_task = None


async def cleanup_loop():
    """Background task that runs cleanup every hour."""
    while True:
        try:
            # Wait 1 hour between cleanup runs
            await asyncio.sleep(3600)

            logger.info("Running session cleanup task")
            cleaned_count = await cleanup_old_sessions(hours=settings.session_ttl_hours)
            logger.info(f"Cleaned up {cleaned_count} old sessions from GCS")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


async def start_cleanup_task():
    """Start the background cleanup task."""
    global _cleanup_task
    if _cleanup_task is None:
        _cleanup_task = asyncio.create_task(cleanup_loop())
        logger.info("Started background cleanup task")


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
        logger.info("Stopped background cleanup task")
