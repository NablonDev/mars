"""
The error contract: `AppError` -> its own status + the unified
`{success, message, data, error}` envelope (`app/core/envelope.py`), anything
else -> an opaque 500, and a request id on every response.

Rewritten (Phase 7a) against the Phase 6 exception-hierarchy collapse: the
old `{"error": {"code", "message"}}` shape and the old single-arg
`AppError("msg")`/`ServiceError(code, msg, status_code=...)` constructors are
both gone -- every `AppError` now takes `(code, message, details=None)`, and
the response body is `{"success", "message", "data", "error": {"code",
"details"}}` (the human-readable text moved to the envelope's top-level
`message`, not nested inside `error`).

Routes are attached to the real app built by the `app` fixture rather than
to a throwaway `FastAPI()`, so these exercise the actual middleware stack
and handler registration from `create_app()`.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    ExternalServiceError,
    NotAuthenticatedError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import REQUEST_ID_HEADER

LEAKY_DETAIL = "postgresql://svc_user:sup3rs3cret@db.internal:5432/mars died mid-query"


@pytest.fixture
def error_client(app) -> TestClient:
    """`raise_server_exceptions=False` so the catch-all `Exception` handler's
    response is returned instead of the exception being re-raised into the
    test -- Starlette's `ServerErrorMiddleware` does both."""

    @app.get("/api/v1/_test/not-found")
    def _not_found() -> None:
        raise NotFoundError(code="NOT_FOUND", message="No such thing")

    @app.get("/api/v1/_test/invalid")
    def _invalid() -> None:
        raise ValidationError(code="VALIDATION_ERROR", message="Nothing to work with")

    @app.get("/api/v1/_test/conflict")
    def _conflict() -> None:
        raise ConflictError(code="ALREADY_EXISTS", message="Already exists.")

    @app.get("/api/v1/_test/business-rule")
    def _business_rule() -> None:
        raise BusinessRuleError(code="NO_ACTIVE_RULES", message="No active rules.")

    @app.get("/api/v1/_test/not-authenticated")
    def _not_authenticated() -> None:
        raise NotAuthenticatedError(code="NOT_AUTHENTICATED", message="Not authenticated.")

    @app.get("/api/v1/_test/upstream")
    def _upstream() -> None:
        raise ExternalServiceError(
            code="EXTERNAL_SERVICE_ERROR", message="Upstream call failed", details=LEAKY_DETAIL
        )

    @app.get("/api/v1/_test/boom")
    def _boom() -> None:
        raise RuntimeError(f"unhandled crash: {LEAKY_DETAIL}")

    @app.get("/api/v1/_test/invalid-with-details")
    def _invalid_with_details() -> None:
        raise ValidationError(
            code="INVALID_FIELD", message="Bad field name.", details={"invalid_fields": ["nope"]}
        )

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("not-found", 404, "NOT_FOUND"),
        ("invalid", 422, "VALIDATION_ERROR"),
        ("conflict", 409, "ALREADY_EXISTS"),
        ("business-rule", 409, "NO_ACTIVE_RULES"),
        ("not-authenticated", 401, "NOT_AUTHENTICATED"),
        ("upstream", 502, "EXTERNAL_SERVICE_ERROR"),
    ],
)
def test_app_error_maps_to_its_own_status_and_envelope(error_client, path, status, code):
    resp = error_client.get(f"/api/v1/_test/{path}")
    body = resp.json()

    assert resp.status_code == status
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == code
    assert set(body["error"]) == {"code", "details"}


def test_app_error_message_reaches_the_client(error_client):
    resp = error_client.get("/api/v1/_test/not-found")

    assert resp.json()["message"] == "No such thing"


def test_app_error_detail_is_never_serialized_for_5xx(error_client):
    resp = error_client.get("/api/v1/_test/upstream")

    assert resp.status_code == 502
    assert resp.json()["message"] == "Upstream call failed"
    assert resp.json()["error"]["details"] is None
    assert "sup3rs3cret" not in resp.text
    assert "db.internal" not in resp.text


def test_app_error_details_reach_the_client_for_4xx(error_client):
    resp = error_client.get("/api/v1/_test/invalid-with-details")

    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == {"invalid_fields": ["nope"]}


def test_unhandled_exception_returns_a_generic_500_with_no_internal_detail(error_client):
    resp = error_client.get("/api/v1/_test/boom")
    body = resp.json()

    assert resp.status_code == 500
    assert body["success"] is False
    assert body["message"] == "Internal server error"
    assert body["error"] == {"code": "INTERNAL_ERROR", "details": None}
    # No exception message, no exception type name, no stack trace, no DSN.
    assert "unhandled crash" not in resp.text
    assert "RuntimeError" not in resp.text
    assert "sup3rs3cret" not in resp.text
    assert "Traceback" not in resp.text


def test_unhandled_exception_is_logged_in_full_with_the_request_id(error_client, caplog):
    caplog.set_level(logging.ERROR, logger="app.core.exceptions")

    resp = error_client.get("/api/v1/_test/boom", headers={REQUEST_ID_HEADER: "trace-abc-123"})

    record = next(r for r in caplog.records if r.name == "app.core.exceptions")
    assert record.request_id == "trace-abc-123"
    assert record.status == 500
    assert record.path == "/api/v1/_test/boom"
    assert record.exc_info is not None  # the traceback the response withheld
    assert resp.status_code == 500


def test_app_error_detail_is_logged_even_though_it_is_withheld(error_client, caplog):
    caplog.set_level(logging.WARNING, logger="app.core.exceptions")

    error_client.get("/api/v1/_test/upstream")

    record = next(r for r in caplog.records if r.name == "app.core.exceptions")
    assert "sup3rs3cret" in record.getMessage()
    assert record.error_code == "EXTERNAL_SERVICE_ERROR"


def test_client_errors_are_logged_without_a_traceback(error_client, caplog):
    caplog.set_level(logging.WARNING, logger="app.core.exceptions")

    error_client.get("/api/v1/_test/not-found")

    record = next(r for r in caplog.records if r.name == "app.core.exceptions")
    assert record.levelno == logging.WARNING
    assert record.exc_info is None


def test_request_id_is_generated_and_echoed_on_the_response(error_client):
    resp = error_client.get("/api/v1/health")

    request_id = resp.headers[REQUEST_ID_HEADER]
    assert len(request_id) == 32
    assert request_id.isalnum()


def test_a_safe_inbound_request_id_is_reused(error_client):
    resp = error_client.get("/api/v1/health", headers={REQUEST_ID_HEADER: "client-supplied-id.1"})

    assert resp.headers[REQUEST_ID_HEADER] == "client-supplied-id.1"


@pytest.mark.parametrize(
    "unsafe",
    [
        "id with spaces",
        "id;rm -rf /",
        "x" * 65,
        "",
    ],
)
def test_an_unsafe_inbound_request_id_is_replaced_not_echoed(error_client, unsafe):
    """The header value lands in the log stream, so it is validated rather
    than trusted -- length-bounded and restricted to characters that can't
    forge a second log line."""
    resp = error_client.get("/api/v1/health", headers={REQUEST_ID_HEADER: unsafe})

    assert resp.headers[REQUEST_ID_HEADER] != unsafe
    assert len(resp.headers[REQUEST_ID_HEADER]) == 32


def test_request_id_is_echoed_on_an_error_response_too(error_client):
    """Including the 500 path, which Starlette handles outside the
    request-id middleware -- the handler sets the header itself."""
    for path in ("not-found", "boom"):
        resp = error_client.get(f"/api/v1/_test/{path}", headers={REQUEST_ID_HEADER: f"trace-{path}"})
        assert resp.headers[REQUEST_ID_HEADER] == f"trace-{path}"


def test_access_log_records_method_path_status_and_duration(error_client, caplog):
    caplog.set_level(logging.INFO, logger="app.access")

    resp = error_client.get("/api/v1/_test/not-found")

    record = next(r for r in caplog.records if r.name == "app.access")
    assert record.method == "GET"
    assert record.path == "/api/v1/_test/not-found"
    assert record.status == 404
    assert record.duration_ms >= 0
    assert record.request_id == resp.headers[REQUEST_ID_HEADER]


def test_access_log_reports_500_for_an_unhandled_exception(error_client, caplog):
    caplog.set_level(logging.INFO, logger="app.access")

    error_client.get("/api/v1/_test/boom")

    record = next(r for r in caplog.records if r.name == "app.access")
    assert record.status == 500


def test_access_log_duration_excludes_background_task_time(app, caplog):
    """Regression test: a route that schedules a `BackgroundTasks`
    callback used to have its access-log `duration_ms` include however
    long that background task took to run, because Starlette runs
    background tasks *inside* the same ASGI call this middleware
    originally wrapped in one `finally` block around the whole thing.
    `duration_ms` must reflect what a client actually experienced (up to
    the response being sent), not the background job's own runtime.

    The assertion is relative rather than a fixed absolute cap: under
    full-suite load, `duration_ms` for the request itself can legitimately
    balloon to a few hundred ms from scheduler contention alone, with
    nothing to do with the background task.
    """
    import time as time_module

    from fastapi import BackgroundTasks

    background_task_sleep_s = 3.0

    def _slow_background_task() -> None:
        time_module.sleep(background_task_sleep_s)

    @app.get("/api/v1/_test/with-background-task")
    def _with_background_task(background_tasks: BackgroundTasks) -> dict:
        background_tasks.add_task(_slow_background_task)
        return {"status": "scheduled"}

    caplog.set_level(logging.INFO, logger="app.access")
    client = TestClient(app)

    client.get("/api/v1/_test/with-background-task")

    record = next(r for r in caplog.records if r.name == "app.access")
    background_task_sleep_ms = background_task_sleep_s * 1000
    assert record.duration_ms < background_task_sleep_ms / 2


def test_fastapi_request_validation_still_returns_its_own_422_contract(client):
    """The new handlers still map `RequestValidationError` into the unified
    envelope (`code="REQUEST_VALIDATION_ERROR"`), not FastAPI's raw
    `{"detail": [...]}` shape -- see `register_exception_handlers`."""
    resp = client.post("/api/v1/purchase-orders", json={"purchase_order_number": "MISSING-EVERYTHING-ELSE"})

    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert isinstance(body["error"]["details"]["errors"], list)


def test_bare_http_exception_is_reshaped_into_the_unified_envelope(app):
    """`require_internal_api_key`'s bare `HTTPException(401)` (and any other
    bare `HTTPException`) is reshaped through the same envelope as
    `AppError` -- see `register_exception_handlers`'s `HTTPException`
    handler docstring."""
    from app.api.dependencies import require_internal_api_key

    del app.dependency_overrides[require_internal_api_key]
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/v1/purchase-orders")

    assert resp.status_code == 401
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "HTTP_401"
