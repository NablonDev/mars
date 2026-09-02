"""Application exception types and FastAPI handlers for consistent HTTP error responses.

Collapsed (Phase 6) from 14 leaf `AppError` subclasses plus a separate
`ServiceError` type into 6 high-level categories matching HTTP semantics,
each parametrized by a required `code: str` at construction rather than a
dedicated subclass per failure case -- see the phase report for the full
old-code -> new-category/`code=` mapping table. `ServiceError` (previously
used ad hoc by cmir/po_validation) is gone; its one extra capability -- an
optional `details: dict | None` -- is now a field on `AppError` itself, and
every one of its string codes (`THREAD_NOT_FOUND`, `CMIR_VERSION_CONFLICT`,
`MATERIAL_NOT_FOUND`, ...) carries over unchanged into whichever category
matches its old `status_code`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import ClassVar

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.envelope import error_envelope
from app.core.logging import REQUEST_ID_HEADER, get_request_id

logger = logging.getLogger(__name__)

GENERIC_500_MESSAGE = "Internal server error"


class AppError(Exception):
    """Base for every expected application error.

    `status_code` is class-level (each of the 6 direct subclasses below
    declares its contract once); `code`/`message`/`details` are supplied at
    each raise site. `message` is the only thing serialized into the API
    response body; `details` is additionally exposed for 4xx responses
    (matching the old `ServiceError` contract) but withheld for 5xx ones,
    since call sites may put internal failure information there -- see
    `register_exception_handlers`.
    """

    status_code: ClassVar[int]

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message if details is None else f"{message} :: {details}")
        self.code = code
        self.message = message
        self.details = details


class NotFoundError(AppError):
    """HTTP 404 -- e.g. ``code="PO_NOT_FOUND"``, ``"NO_PROJECTION_SUMMARY_JOB_EXISTS"``,
    ``"PO_DELIVERY_CHANGE_REQUEST_NOT_FOUND"``, ``"THREAD_NOT_FOUND"``, ``"EMAIL_NOT_FOUND"``."""

    status_code: ClassVar[int] = 404


class ConflictError(AppError):
    """HTTP 409 -- e.g. ``code="PO_ALREADY_EXISTS"``,
    ``"ACTIVE_PO_DELIVERY_CHANGE_REQUEST_EXISTS"``, ``"CMIR_VERSION_CONFLICT"``,
    ``"THREAD_STALE"``, ``"THREAD_NOT_WAITING"``."""

    status_code: ClassVar[int] = 409


class ValidationError(AppError):
    """HTTP 422 -- e.g. ``code="INVALID_PENALTY_RULE_DATA"``, ``"INVALID_AS_OF_DATE"``,
    ``"INVALID_PO_DELIVERY_CHANGE_RESPONSE"``, ``"VALIDATION_ERROR"``, ``"MATERIAL_NOT_FOUND"``,
    ``"VIEW_NOT_SUPPORTED"``."""

    status_code: ClassVar[int] = 422


class BusinessRuleError(AppError):
    """HTTP 409 -- expected-state/domain-rule violations, distinct from
    `ConflictError`'s concurrent-write/duplicate-resource conflicts. E.g.
    ``code="NO_MITIGATION_OPTIONS_EXIST"``, ``"NO_ACTIVE_RULES"``,
    ``"NO_PROJECTION_EXISTS"``, ``"PO_DELIVERY_CHANGE_LEAD_TIME_ERROR"``."""

    status_code: ClassVar[int] = 409


class ExternalServiceError(AppError):
    """HTTP 502 -- unexpected failure of an upstream/downstream dependency
    (Azure OpenAI, a bounded LLM tool loop, LangGraph invoke/resume). E.g.
    ``code="PENALTY_PROJECTION_SUMMARY_UPSTREAM_FAILED"``,
    ``"PENALTY_MITIGATION_SUMMARY_UPSTREAM_FAILED"``, ``"WORKFLOW_RESUME_FAILED"``,
    ``"WORKFLOW_STATE_CORRUPT"``, ``"QUEUE_NOT_CONFIGURED"``."""

    status_code: ClassVar[int] = 502


class NotAuthenticatedError(AppError):
    """HTTP 401 -- unifies the bare FastAPI ``HTTPException(401)`` raised by
    ``require_internal_api_key`` (``app/api/dependencies.py``, not touched
    this phase -- see the generic `HTTPException` handler below)."""

    status_code: ClassVar[int] = 401


def _resolve_request_id(request: Request) -> str:
    """Resolve the request ID from request state, then the request context."""
    return getattr(request.state, "request_id", None) or get_request_id()


def _is_json_safe(value: object) -> bool:
    """Whether `value` survives `json.dumps` unchanged (recursively)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_safe(item) for key, item in value.items())
    return False


def _sanitize_validation_errors(errors: Sequence[dict]) -> list[dict]:
    """Make every error dict from `RequestValidationError.errors()` JSON-safe.

    Pydantic v2 packages a plain `ValueError` raised by a custom
    `model_validator`/`field_validator` into the error dict's `ctx` key as
    the raw exception object itself (``ctx={"error": ValueError(...)}``) --
    not JSON-serializable, and `JSONResponse.render` has no fallback encoder,
    so `json.dumps` raises an unhandled `TypeError` instead of the intended
    422 (surfacing as a 500). Replace any non-JSON-safe `ctx` value with its
    `str()` form instead of dropping it, so the original validator's message
    still reaches the client.
    """
    sanitized = []
    for error in errors:
        ctx = error.get("ctx")
        if isinstance(ctx, dict):
            error = {
                **error,
                "ctx": {key: value if _is_json_safe(value) else str(value) for key, value in ctx.items()},
            }
        sanitized.append(error)
    return sanitized


def register_exception_handlers(app: FastAPI) -> None:
    """Register handlers for expected application and unexpected exceptions.

    Every path below -- `AppError`, FastAPI's native `RequestValidationError`,
    a bare `HTTPException`, and the unhandled-exception catch-all -- produces
    the same `{success, message, data, error}` shape via `error_envelope`
    (`data` is always `None` on an error path).
    """

    @app.exception_handler(AppError)
    async def _app_error_handler(
        request: Request,
        exc: AppError,
    ) -> JSONResponse:
        request_id = _resolve_request_id(request)
        is_server_error = exc.status_code >= 500

        logger.log(
            logging.ERROR if is_server_error else logging.WARNING,
            "%s %s -> %s %s: %s%s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.code,
            exc.message,
            f" | details={exc.details}" if exc.details else "",
            exc_info=exc if is_server_error else None,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": exc.status_code,
                "error_code": exc.code,
            },
        )

        # `details` may contain client-facing validation information for 4xx
        # errors. Do not expose it for 5xx errors, since call sites may put
        # internal failure information there.
        envelope = error_envelope(
            exc.code,
            exc.message,
            details=exc.details if not is_server_error else None,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope.model_dump(),
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = _resolve_request_id(request)

        logger.warning(
            "%s %s -> 422 REQUEST_VALIDATION_ERROR: %s",
            request.method,
            request.url.path,
            exc.errors(),
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": 422,
                "error_code": "REQUEST_VALIDATION_ERROR",
            },
        )

        envelope = error_envelope(
            "REQUEST_VALIDATION_ERROR",
            "Request validation failed.",
            details={"errors": _sanitize_validation_errors(exc.errors())},
        )
        return JSONResponse(
            status_code=422,
            content=envelope.model_dump(),
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        # Reshapes FastAPI/Starlette's own bare `HTTPException`s (e.g. the
        # 401 today's `require_internal_api_key` raises directly, since
        # `app/api/dependencies.py` is out of scope this phase) into the
        # same envelope shape as `AppError`, so Phase 7's route handlers
        # don't have to solve this from scratch.
        request_id = _resolve_request_id(request)
        is_server_error = exc.status_code >= 500

        logger.log(
            logging.ERROR if is_server_error else logging.WARNING,
            "%s %s -> %s HTTP_%s: %s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.status_code,
            exc.detail,
            exc_info=exc if is_server_error else None,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": exc.status_code,
                "error_code": f"HTTP_{exc.status_code}",
            },
        )

        envelope = error_envelope(f"HTTP_{exc.status_code}", str(exc.detail))
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope.model_dump(),
            headers={REQUEST_ID_HEADER: request_id, **(exc.headers or {})},
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
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
                "error_code": "INTERNAL_ERROR",
            },
        )

        envelope = error_envelope("INTERNAL_ERROR", GENERIC_500_MESSAGE)
        return JSONResponse(
            status_code=500,
            content=envelope.model_dump(),
            headers={REQUEST_ID_HEADER: request_id},
        )
