"""
The one domain-exception hierarchy, plus the FastAPI handlers that map it
onto HTTP. Services and repositories raise these; no route does its own
exception-to-status translation, and nothing outside this module decides
what an error looks like on the wire.

Two-field split, deliberately:

- `message` is client-safe and is the only thing that reaches the
  response body (`{"error": {"code", "message"}}` --
  app/schemas/common.py::ErrorResponse).
- `detail` is server-side only. Anything that can carry vendor
  internals, SQL, a connection string or stack-trace-shaped text (an
  Azure OpenAI SDK exception, say) belongs here: it is logged and never
  serialized. See ./CLAUDE.local.md's "Do Not: return raw stack traces,
  secrets, or internal error detail in API responses".

`ExternalServiceError` defaults to 502; a subclass that means "we are up
but a dependency is not" sets `status_code = 503`.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import REQUEST_ID_HEADER, get_request_id
from app.schemas.common import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

GENERIC_500_MESSAGE = "Internal server error"


class AppError(Exception):
    """Base for every error this application raises on purpose.

    `code`/`status_code` are class-level so a subclass declares its
    contract once; `message`/`detail` are per-raise.
    """

    code: ClassVar[str] = "INTERNAL_ERROR"
    status_code: ClassVar[int] = 500

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        # `str(exc)` keeps both halves so a traceback/log/CLI stays
        # informative; only `.message` is ever serialized to a client.
        super().__init__(message if detail is None else f"{message} :: {detail}")
        self.message = message
        self.detail = detail


class NotFoundError(AppError):
    code: ClassVar[str] = "NOT_FOUND"
    status_code: ClassVar[int] = 404


class ConflictError(AppError):
    code: ClassVar[str] = "CONFLICT"
    status_code: ClassVar[int] = 409


class ValidationError(AppError):
    """Well-formed request, unprocessable given current server state --
    422, the same code FastAPI/Pydantic use for schema-level rejections.
    Not to be confused with `pydantic.ValidationError`."""

    code: ClassVar[str] = "VALIDATION_ERROR"
    status_code: ClassVar[int] = 422


class ExternalServiceError(AppError):
    code: ClassVar[str] = "EXTERNAL_SERVICE_ERROR"
    status_code: ClassVar[int] = 502


class OrderNotFoundError(NotFoundError):
    """No `dim_order` row for this business `order_id`. One class for
    every "unknown order" site (OrderRepository, ProjectionService,
    ExplanationService, SeedingService) so the message and the status code
    are decided in exactly one place."""

    code: ClassVar[str] = "ORDER_NOT_FOUND"

    def __init__(self, order_id: str, *, hint: str | None = None) -> None:
        message = f"No order found with order_id={order_id!r}"
        if hint:
            message = f"{message}. {hint}"
        super().__init__(message)
        self.order_id = order_id


class NoActiveRulesError(ValidationError):
    """Raised when a retailer has no active fine rules -- distinct from
    "order not found" so the caller gets a clear 422 rather than a 404 or
    a 500."""

    code: ClassVar[str] = "NO_ACTIVE_RULES"


class NoProjectionExistsError(ValidationError):
    """Raised when an order has zero `fact_projected_fine` rows -- there's
    nothing to explain yet, distinct from "order not found" so it maps to
    a 422 instead of a 404."""

    code: ClassVar[str] = "NO_PROJECTION_EXISTS"


class InvalidAsOfDateError(ValidationError):
    """Raised when a caller-supplied `as_of_date` falls outside the valid
    range for this order -- after today, or before the order's earliest
    projection date.

    Without this check, `as_of_date` is a client-supplied, unvalidated
    field with no relationship to real projection history -- an
    unauthenticated caller could otherwise mint an unbounded number of
    distinct cache keys (one real Azure OpenAI call each, up to
    MAX_TOOL_ROUNDS round-trips) just by varying it per request. Bounding
    it to [earliest projection date, today] closes that vector without
    needing request-level rate limiting."""

    code: ClassVar[str] = "INVALID_AS_OF_DATE"


class ToolLoopExhaustedError(ExternalServiceError):
    """Raised when the model still hasn't returned valid structured output
    after the bounded tool-calling loop's forced final round. An upstream
    (Azure OpenAI) failure, not a client input error -- hence 502.

    Raise it with the underlying SDK exception in `detail`, never in
    `message`: that text can embed vendor internals and stack-trace-shaped
    content that must not reach a response body."""

    code: ClassVar[str] = "EXPLANATION_UPSTREAM_FAILED"


def _error_body(code: str, message: str) -> dict:
    return ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump()


def _resolve_request_id(request: Request) -> str:
    """`request.state` first, `ContextVar` second. The catch-all handler
    runs in Starlette's `ServerErrorMiddleware`, which sits *outside* the
    request-id middleware -- by then the ContextVar has already been reset,
    but the value stashed on the scope's state survives."""
    return getattr(request.state, "request_id", None) or get_request_id()


def register_exception_handlers(app: FastAPI) -> None:
    """The whole error contract, in two handlers.

    Registered in app/main.py::create_app. FastAPI's own
    `RequestValidationError`/`HTTPException` handlers are left alone --
    their `{"detail": ...}` bodies are the documented framework contract.
    """

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = _resolve_request_id(request)
        # Logged once, here, at the boundary -- not re-logged at every
        # layer it bubbled through. 5xx gets a traceback; a 4xx is a
        # client mistake and doesn't need one.
        is_server_error = exc.status_code >= 500
        logger.log(
            logging.ERROR if is_server_error else logging.WARNING,
            "%s %s -> %s %s: %s%s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.code,
            exc.message,
            f" | detail={exc.detail}" if exc.detail else "",
            exc_info=exc if is_server_error else None,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": exc.status_code,
                "error_code": exc.code,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Anything that reaches here is a bug, not a contract. Full
        # traceback to the log, a fixed opaque body to the client: no
        # exception message, no type name, no stack trace.
        request_id = _resolve_request_id(request)
        logger.error(
            "%s %s -> 500 unhandled %s",
            request.method,
            request.url.path,
            type(exc).__name__,
            exc_info=exc,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": 500,
                "error_code": AppError.code,
            },
        )
        # The header is set here, not left to `RequestIdMiddleware`: this
        # handler runs inside Starlette's `ServerErrorMiddleware`, which sits
        # *outside* that middleware, so its response never passes through
        # the wrapped `send`. A 500 is the response a caller most needs a
        # correlation id on.
        return JSONResponse(
            status_code=500,
            content=_error_body(AppError.code, GENERIC_500_MESSAGE),
            headers={REQUEST_ID_HEADER: request_id},
        )
