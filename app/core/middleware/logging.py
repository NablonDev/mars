"""ASGI middleware for structured HTTP access logging."""

from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.access")


class AccessLogMiddleware:
    """ASGI middleware that logs one line per HTTP request with method, path, status, and duration."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500
        logged = False

        def log_once() -> None:
            """Emit the access-log line once, guarding against the send hook and the finally block both firing."""
            nonlocal logged

            if logged:
                return

            logged = True
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            method = scope.get("method", "-")
            path = scope.get("path", "-")

            logger.info(
                "%s %s %s %sms",
                method,
                path,
                status,
                duration_ms,
                extra={
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": duration_ms,
                },
            )

        async def send_with_status(message: Message) -> None:
            """Capture the response status, forward the message, then log once the body is fully sent."""
            nonlocal status

            if message["type"] == "http.response.start":
                status = message["status"]

            await send(message)

            # Log when the response body is actually sent, rather than when
            # the ASGI application returns (which can include background work).
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                log_once()

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            # Covers requests that fail before a response body is sent.
            log_once()
