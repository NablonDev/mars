"""
Pure-ASGI middleware, deliberately not Starlette's `BaseHTTPMiddleware`:
these run in the same task as the endpoint, so the request-id
`ContextVar` set in `RequestIdMiddleware` is visible to every log call
downstream, and the access-log middleware sees an unhandled exception
propagate rather than losing it behind a task boundary.
"""

from app.core.middleware.logging import AccessLogMiddleware
from app.core.middleware.request_id import RequestIdMiddleware

__all__ = ["AccessLogMiddleware", "RequestIdMiddleware"]
