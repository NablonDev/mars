"""Unified `{success, message, data, error}` API response envelope.

`app/core/exceptions.py::register_exception_handlers` is this module's one
consumer for now -- every error response (`AppError`, FastAPI's own
`RequestValidationError`/`HTTPException`, and the unhandled-exception
catch-all) is shaped through `error_envelope`. Phase 7 wires route handlers
to `success_envelope`/`Envelope[T]` as `response_model=Envelope[SomeResponse]`
so success and error responses share one shape end to end.
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    details: str | dict | None = None


class Envelope[T](BaseModel):
    success: bool
    message: str
    data: T | None = None
    error: ErrorBody | None = None


def success_envelope[T](data: T, message: str = "OK") -> Envelope[T]:
    return Envelope[T](success=True, message=message, data=data, error=None)


def error_envelope(code: str, message: str, details: str | dict | None = None) -> Envelope[None]:
    return Envelope[None](
        success=False, message=message, data=None, error=ErrorBody(code=code, details=details)
    )
