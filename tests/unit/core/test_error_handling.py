"""
The error contract: `AppError` -> its own status + the
`{"error": {"code", "message"}}` envelope, anything else -> an opaque 500,
and a request id on every response.

Routes are attached to the real app built by the `app` fixture rather than
to a throwaway `FastAPI()`, so these exercise the actual middleware stack
and handler registration from `create_app()`.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import (
    AppError,
    ExternalServiceError,
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
        raise NotFoundError("No such thing")

    @app.get("/api/v1/_test/invalid")
    def _invalid() -> None:
        raise ValidationError("Nothing to work with")

    @app.get("/api/v1/_test/upstream")
    def _upstream() -> None:
        raise ExternalServiceError("Upstream call failed", detail=LEAKY_DETAIL)

    @app.get("/api/v1/_test/server-app-error")
    def _server_app_error() -> None:
        raise AppError("Something broke", detail=LEAKY_DETAIL)

    @app.get("/api/v1/_test/boom")
    def _boom() -> None:
        raise RuntimeError(f"unhandled crash: {LEAKY_DETAIL}")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("not-found", 404, "NOT_FOUND"),
        ("invalid", 422, "VALIDATION_ERROR"),
        ("upstream", 502, "EXTERNAL_SERVICE_ERROR"),
        ("server-app-error", 500, "INTERNAL_ERROR"),
    ],
)
def test_app_error_maps_to_its_own_status_and_envelope(error_client, path, status, code):
    resp = error_client.get(f"/api/v1/_test/{path}")

    assert resp.status_code == status
    assert set(resp.json()) == {"error"}
    assert resp.json()["error"]["code"] == code
    assert set(resp.json()["error"]) == {"code", "message"}


def test_app_error_message_reaches_the_client(error_client):
    resp = error_client.get("/api/v1/_test/not-found")

    assert resp.json()["error"]["message"] == "No such thing"


def test_app_error_detail_is_never_serialized(error_client):
    resp = error_client.get("/api/v1/_test/upstream")

    assert resp.status_code == 502
    assert resp.json()["error"]["message"] == "Upstream call failed"
    assert "sup3rs3cret" not in resp.text
    assert "db.internal" not in resp.text


def test_unhandled_exception_returns_a_generic_500_with_no_internal_detail(error_client):
    resp = error_client.get("/api/v1/_test/boom")

    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}}
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
    Confirmed live against the real fine-summary background job before
    this fix -- a `202` a client received in under a second was logged as
    if the request had taken 90+ seconds. `duration_ms` must reflect what
    a client actually experienced (up to the response being sent), not
    the background job's own runtime."""
    import time as time_module

    from fastapi import BackgroundTasks

    def _slow_background_task() -> None:
        time_module.sleep(0.2)

    @app.get("/api/v1/_test/with-background-task")
    def _with_background_task(background_tasks: BackgroundTasks) -> dict:
        background_tasks.add_task(_slow_background_task)
        return {"status": "scheduled"}

    caplog.set_level(logging.INFO, logger="app.access")
    client = TestClient(app)

    client.get("/api/v1/_test/with-background-task")

    record = next(r for r in caplog.records if r.name == "app.access")
    # The background task slept 200ms -- the logged duration must not
    # include that.
    assert record.duration_ms < 100


def test_fastapi_request_validation_still_returns_its_own_422_contract(client):
    """The new handlers deliberately don't touch `RequestValidationError` --
    `{"detail": [...]}` is FastAPI's documented contract and clients
    (including the OpenAPI schema) depend on it."""
    resp = client.post("/api/v1/orders", json={"order_id": "MISSING-EVERYTHING-ELSE"})

    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)
