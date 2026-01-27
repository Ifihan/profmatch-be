import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class TimingMiddleware(BaseHTTPMiddleware):
    """Middleware to log request processing time."""

    def _format_duration(self, duration_seconds: float) -> str:
        """Format duration in human-readable format."""
        if duration_seconds < 1:
            # Less than 1 second - show in milliseconds
            return f"{duration_seconds * 1000:.2f}ms"
        elif duration_seconds < 60:
            # Less than 1 minute - show in seconds
            return f"{duration_seconds:.2f}s"
        else:
            # 1 minute or more - show in minutes and seconds
            minutes = int(duration_seconds // 60)
            seconds = duration_seconds % 60
            return f"{minutes}m {seconds:.2f}s"

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # Process the request
        response: Response = await call_next(request)

        # Calculate duration
        duration = time.time() - start_time
        formatted_duration = self._format_duration(duration)

        # Log the request with timing
        logger.info(
            f"{request.method} {request.url.path} - "
            f"Status: {response.status_code} - "
            f"Duration: {formatted_duration}"
        )

        # Optionally add the duration to response headers
        response.headers["X-Process-Time"] = formatted_duration

        return response