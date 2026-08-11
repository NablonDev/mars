from typing import Literal

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """The one error envelope every deliberate error uses -- built in
    app/core/exceptions.py::register_exception_handlers."""

    error: ErrorBody


class HealthResponse(BaseModel):
    """Returned by GET /health with a 200 when `status == "ok"` and a 503
    when it's `degraded`, so a load balancer can act on the status code
    without parsing the body."""

    status: Literal["ok", "degraded"]
    database: Literal["ok", "unreachable"]
