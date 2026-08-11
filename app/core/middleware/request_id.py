"""
Correlation id per request: reuse the caller's `X-Request-ID` when it
looks safe, otherwise mint one. Published three ways -- on
`request.state.request_id` (handlers), in the `ContextVar` (logging), and
echoed back on the response header (the client can quote it in a bug
report).
"""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import REQUEST_ID_HEADER, reset_request_id, set_request_id

# An inbound header value goes straight into the log stream, so it is
# validated rather than trusted: no newlines (log injection), no control
# characters, and a hard length bound. Anything else is discarded and
# replaced with a generated id.
_SAFE_REQUEST_ID = re.compile(r"\A[A-Za-z0-9._:\-]{1,64}\Z")


def _inbound_request_id(scope: Scope) -> str | None:
    candidate = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if candidate and _SAFE_REQUEST_ID.match(candidate):
        return candidate
    return None


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope) or uuid4().hex
        # Backing store for `request.state` -- Starlette may already have
        # populated it from lifespan state, so extend rather than replace.
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)
