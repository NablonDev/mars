"""Application exception types and FastAPI handlers for consistent HTTP error responses."""

from __future__ import annotations

import logging
from datetime import date
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import REQUEST_ID_HEADER, get_request_id
from app.schemas.common import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

GENERIC_500_MESSAGE = "Internal server error"


class AppError(Exception):
    """Base exception for expected application errors. ``code``/``status_code``
    are class-level (subclasses declare their contract once); ``message``/``detail``
    are per-instance."""

    code: ClassVar[str] = "INTERNAL_ERROR"
    status_code: ClassVar[int] = 500

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        # Keep both message and detail available in logs and tracebacks,
        # while only exposing the client-safe message in API responses.
        super().__init__(message if detail is None else f"{message} :: {detail}")
        self.message = message
        self.detail = detail


class NotFoundError(AppError):
    code: ClassVar[str] = "NOT_FOUND"
    status_code: ClassVar[int] = 404


class ValidationError(AppError):
    """Expected application-state validation failure, returned as HTTP 422."""

    code: ClassVar[str] = "VALIDATION_ERROR"
    status_code: ClassVar[int] = 422


class ExternalServiceError(AppError):
    code: ClassVar[str] = "EXTERNAL_SERVICE_ERROR"
    status_code: ClassVar[int] = 502


class OrderNotFoundError(NotFoundError):
    """Raised when no order exists for the requested order ID."""

    code: ClassVar[str] = "ORDER_NOT_FOUND"

    def __init__(self, order_id: str, *, hint: str | None = None) -> None:
        message = f"No order found with order_id={order_id!r}"
        if hint:
            message = f"{message}. {hint}"
        super().__init__(message)
        self.order_id = order_id


class NoActiveRulesError(ValidationError):
    """Raised when a retailer has no active fine rules."""

    code: ClassVar[str] = "NO_ACTIVE_RULES"


class NoProjectionExistsError(ValidationError):
    """Raised when an order has no projection results."""

    code: ClassVar[str] = "NO_PROJECTION_EXISTS"


class InvalidAsOfDateError(ValidationError):
    """Raised when an as-of date is outside the allowed projection range."""

    code: ClassVar[str] = "INVALID_AS_OF_DATE"


class NoSummaryJobExistsError(NotFoundError):
    """Raised when no fine-summary job exists for the requested parameters."""

    code: ClassVar[str] = "NO_SUMMARY_JOB_EXISTS"

    def __init__(self, order_id: str, as_of_date: date) -> None:
        super().__init__(
            f"No fine-summary job found for order_id={order_id!r}, "
            f"as_of_date={as_of_date.isoformat()!r} -- POST /orders/{{order_id}}/summary first."
        )
        self.order_id = order_id
        self.as_of_date = as_of_date


class ToolLoopExhaustedError(ExternalServiceError):
    """Raised when the fine-summary upstream tool loop cannot produce a usable result."""

    code: ClassVar[str] = "FINE_SUMMARY_UPSTREAM_FAILED"


class ConflictError(AppError):
    code: ClassVar[str] = "CONFLICT"
    status_code: ClassVar[int] = 409


class OrderAlreadyExistsError(ConflictError):
    code: ClassVar[str] = "ORDER_ALREADY_EXISTS"

    def __init__(self, order_id: str) -> None:
        super().__init__(f"Order {order_id!r} already exists")
        self.order_id = order_id


class InvalidFineRuleDataError(AppError):
    """Raised when stored fine-rule reference data is internally inconsistent."""

    code: ClassVar[str] = "INVALID_FINE_RULE_DATA"


def _error_body(code: str, message: str) -> dict:
    return ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump()


def _resolve_request_id(request: Request) -> str:
    """Resolve the request ID from request state, then the request context."""
    return getattr(request.state, "request_id", None) or get_request_id()


def register_exception_handlers(app: FastAPI) -> None:
    """Register handlers for expected application and unexpected exceptions."""

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
                "error_code": AppError.code,
            },
        )

        return JSONResponse(
            status_code=500,
            content=_error_body(AppError.code, GENERIC_500_MESSAGE),
            headers={REQUEST_ID_HEADER: request_id},
        )
