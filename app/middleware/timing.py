import json
import logging
import time

logger = logging.getLogger(__name__)


class TimingMiddleware:
    """Pure ASGI middleware to log request processing time."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                duration_ms = int((time.time() - start_time) * 1000)
                headers = list(message.get("headers", []))
                headers.append((b"x-process-time", f"{duration_ms}ms".encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration_ms = int((time.time() - start_time) * 1000)
        path = scope.get("path", "")
        method = scope.get("method", "")

        logger.info(json.dumps({
            "event": "http_request",
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }))
