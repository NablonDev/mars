"""
Access log: one line per request with method, path, status and
duration_ms. The request id comes from the `ContextVar`
(app/core/logging.py) via the logging filter, so it isn't passed
explicitly here.

Only `scope["path"]`, never the raw query string or full URL -- a query
string is caller-controlled and is exactly the place a token or password
shows up in someone's `curl`.
"""

from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.access")


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        # 500 is the honest default: if the response never starts, the
        # request died on an exception that Starlette's ServerErrorMiddleware
        # -- which sits outside this middleware -- turned into a 500.
        status = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            method = scope.get("method", "-")
            path = scope.get("path", "-")
            # No exc_info here: the exception handlers log the traceback
            # once, at the boundary. This line is the access record.
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
