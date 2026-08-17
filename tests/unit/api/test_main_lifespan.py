"""Lifespan-level tests for app/main.py's job-queue wiring.

Uses a real `with TestClient(app) as client:` block (not the `app`/`client`
fixtures from conftest.py, which bypass lifespan entirely via dependency
overrides -- see conftest.py's module docstring) specifically so lifespan
startup/shutdown actually run.
"""

from uuid import UUID

import pytest

import app.main as main_module
from app.main import create_app


class _FakeJobQueue:
    """Minimal JobDispatcher+JobSource double -- only `close()` matters here."""

    def __init__(self) -> None:
        self.close_calls = 0

    def dispatch(self, job_item_id: UUID, *, delay_seconds: int = 0) -> None:
        raise AssertionError("dispatch should not be called in this test")

    def close(self) -> None:
        self.close_calls += 1


def test_lifespan_closes_the_job_queue_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_queue = _FakeJobQueue()
    monkeypatch.setattr(main_module, "build_job_queue", lambda settings, database: (fake_queue, fake_queue))

    from fastapi.testclient import TestClient

    app = create_app()

    with TestClient(app) as client:
        assert app.state.job_queue == (fake_queue, fake_queue)
        assert fake_queue.close_calls == 0
        # Not exercising any route here -- just proving lifespan wiring;
        # HTTP-level behavior is covered by the API test suite.
        del client

    assert fake_queue.close_calls == 1


def test_lifespan_builds_the_queue_once_and_reuses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    build_calls: list[object] = []

    def _spy_build_job_queue(settings, database):
        fake_queue = _FakeJobQueue()
        build_calls.append(fake_queue)
        return fake_queue, fake_queue

    monkeypatch.setattr(main_module, "build_job_queue", _spy_build_job_queue)

    from fastapi.testclient import TestClient

    app = create_app()

    with TestClient(app) as client:
        client.get("/api/v1/health")
        client.get("/api/v1/health")

    # Built exactly once for the whole process lifetime, not once per request.
    assert len(build_calls) == 1
