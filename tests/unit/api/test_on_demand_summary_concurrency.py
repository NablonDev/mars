"""Tests for the on-demand (FastAPI BackgroundTasks) concurrency governor
added to `app.api.dependencies.get_fine_projection_summary_job_runner`.

Every test here calls the callable `get_fine_projection_summary_job_runner` returns
directly -- the same pattern `tests/test_api_fine_projection_summary_job_queue.py`
already uses for `test_lost_claim_does_not_execute_generation` -- rather
than going through `TestClient`/`BackgroundTasks`, since `TestClient` runs
background tasks synchronously (see that file's own module docstring) and
so cannot exercise real concurrent execution. `FineProjectionSummaryService` itself
is monkeypatched out entirely (a small fake standing in for it) so these
tests exercise the semaphore/rate-limit-gate wiring in isolation, not the
LLM tool loop -- no live API calls anywhere in this file.

Every test monkeypatches the module-level `_ON_DEMAND_SUMMARY_SEMAPHORE`/
`_ON_DEMAND_RATE_LIMIT_GATE` rather than relying on the real ones built at
import time -- they are shared, process-wide singletons by design (see
their own comment in `app/api/dependencies.py`), so a test that mutated
the real objects would leak state into every other test in the session.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any
from uuid import UUID

import pytest

from app.api import dependencies
from app.api.dependencies import get_fine_projection_summary_job_runner
from app.core.config import Settings
from app.core.rate_limit import RateLimitGate
from app.db.session import Database
from app.models import JobItem
from app.repositories.job_queue import JobQueueRepository

PROMPT_VERSION = "v3-test"


@pytest.fixture(autouse=True)
def _ensure_dependencies_logger_enabled():
    """Guards against the same landmine documented in
    `tests/test_worker_loop.py`'s `_ensure_worker_loop_logger_enabled`:
    `alembic/env.py` calls `logging.config.fileConfig(...)` with its
    default `disable_existing_loggers=True` whenever a migration runs
    in-process in this same pytest session (`tests/test_migration_parity.py`
    does exactly that), which permanently disables every logger that
    already existed at that point -- including `app.api.dependencies`'s
    own logger -- breaking `caplog`-based assertions below regardless of
    run order. Scoped to just this file's logger and restored afterward."""
    logger = logging.getLogger("app.api.dependencies")
    previous = logger.disabled
    logger.disabled = False
    yield
    logger.disabled = previous


class _NoopService:
    """Stands in for `FineProjectionSummaryService` when a test only cares about
    the semaphore/gate wiring around it, not generation itself."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def run_generation(self, order_id: str, as_of_date: date, prompt_version: str) -> None:
        pass


class _FailingService:
    def __init__(self, **kwargs: Any) -> None:
        pass

    def run_generation(self, order_id: str, as_of_date: date, prompt_version: str) -> None:
        raise RuntimeError("simulated generation failure")


def _make_runner(database: Database, *, settings: Settings | None = None):
    # `llm=object()` is never invoked in any test in this file --
    # `FineProjectionSummaryService` itself is always monkeypatched out before the
    # runner is called, so nothing here ever calls a method on `llm`.
    llm = object()
    return get_fine_projection_summary_job_runner(
        database=database,
        llm=llm,  # type: ignore[arg-type]
        settings=settings or Settings(),
    )


def _claim_a_row(database: Database, order_id: str, *, other_worker: bool = False) -> UUID:
    with database.session() as session:
        repo = JobQueueRepository(session)
        run = repo.create_run(run_type="ON_DEMAND", projection_date=date(2026, 8, 9))
        item = repo.enqueue(run["id"], order_id, date(2026, 8, 9), "PROJECTION_SUMMARY_REGEN", max_attempts=5)
        assert item is not None
        job_item_id: UUID = item["id"]
        if other_worker:
            claimed = repo.claim_batch("other-worker", limit=1, job_item_ids=[job_item_id])
            assert len(claimed) == 1
    return job_item_id


# ---------------------------------------------------------------------
# Concurrency is genuinely capped.
# ---------------------------------------------------------------------


def test_on_demand_concurrency_is_capped(database: Database, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "_ON_DEMAND_SUMMARY_SEMAPHORE", threading.BoundedSemaphore(2))

    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}
    release_event = threading.Event()

    class _BlockingService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run_generation(self, order_id: str, as_of_date: date, prompt_version: str) -> None:
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            release_event.wait(timeout=5)
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(dependencies, "FineProjectionSummaryService", _BlockingService)

    run_job = _make_runner(database, settings=Settings(on_demand_summary_acquire_timeout_seconds=5))

    threads = [
        threading.Thread(target=run_job, args=(f"ORD-CAP-{i}", date(2026, 8, 9), PROMPT_VERSION, None))
        for i in range(5)
    ]
    for t in threads:
        t.start()

    # Give every thread time to reach the semaphore and, for the lucky
    # two, start "generation" and block on release_event.
    deadline = time.monotonic() + 2.0
    while state["active"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    release_event.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()

    assert state["max_active"] == 2, "the cap of 2 must never be exceeded, and should be reached"


# ---------------------------------------------------------------------
# Acquire timeout: PENDING, attempt count unchanged, no mark_dead.
# ---------------------------------------------------------------------


def test_acquire_timeout_leaves_the_row_pending_without_consuming_an_attempt(
    database: Database, monkeypatch, caplog
) -> None:
    # A capacity-1 semaphore, pre-acquired so the runner can never get in.
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    monkeypatch.setattr(dependencies, "_ON_DEMAND_SUMMARY_SEMAPHORE", semaphore)
    monkeypatch.setattr(dependencies, "FineProjectionSummaryService", _NoopService)

    with database.session() as session:
        repo = JobQueueRepository(session)
        run = repo.create_run(run_type="ON_DEMAND", projection_date=date(2026, 8, 9))
        item = repo.enqueue(
            run["id"], "ORD-TIMEOUT", date(2026, 8, 9), "PROJECTION_SUMMARY_REGEN", max_attempts=5
        )
        assert item is not None
        job_item_id = item["id"]

    # `on_demand_summary_acquire_timeout_seconds` is an integer Settings
    # field (real production config) -- 0 means "don't block at all,"
    # which is enough to prove the timeout path since the semaphore above
    # is already fully exhausted.
    run_job = _make_runner(database, settings=Settings(on_demand_summary_acquire_timeout_seconds=0))

    import logging

    with caplog.at_level(logging.WARNING, logger="app.api.dependencies"):
        run_job("ORD-TIMEOUT", date(2026, 8, 9), PROMPT_VERSION, job_item_id)

    assert any("concurrency cap reached" in r.message for r in caplog.records)

    with database.session() as session:
        row = session.get(JobItem, job_item_id)
        assert row is not None
        assert row.status == "PENDING"  # never claimed -- untouched
        assert row.attempt_count == 0
        assert row.locked_by is None
        assert row.last_error_code is None  # no mark_dead


# ---------------------------------------------------------------------
# The semaphore is always released: success, failure, lost claim, and
# no leak across repeated (sequential) calls.
# ---------------------------------------------------------------------


def test_semaphore_released_on_success_failure_and_lost_claim_with_no_leak(
    database: Database, monkeypatch
) -> None:
    monkeypatch.setattr(dependencies, "_ON_DEMAND_SUMMARY_SEMAPHORE", threading.BoundedSemaphore(1))
    settings = Settings(on_demand_summary_acquire_timeout_seconds=2)

    # 1. Success path.
    monkeypatch.setattr(dependencies, "FineProjectionSummaryService", _NoopService)
    run_job = _make_runner(database, settings=settings)
    run_job("ORD-A", date(2026, 8, 9), PROMPT_VERSION, None)

    # 2. Failure path -- run_generation raises, caught internally, but the
    # semaphore must still be released.
    monkeypatch.setattr(dependencies, "FineProjectionSummaryService", _FailingService)
    run_job("ORD-B", date(2026, 8, 9), PROMPT_VERSION, None)

    # 3. Lost-claim path -- claimed by "another worker" before this call.
    job_item_id = _claim_a_row(database, "ORD-C", other_worker=True)
    monkeypatch.setattr(dependencies, "FineProjectionSummaryService", _NoopService)
    run_job("ORD-C", date(2026, 8, 9), PROMPT_VERSION, job_item_id)

    # 4. N+1th sequential call: if any prior call had leaked the permit
    # (acquired but never released), the semaphore would already be
    # exhausted and this would block for the full acquire timeout.
    start = time.monotonic()
    run_job("ORD-D", date(2026, 8, 9), PROMPT_VERSION, None)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, "no leaked permit -- this call must acquire promptly"


# ---------------------------------------------------------------------
# The rate-limit gate pauses on-demand work.
# ---------------------------------------------------------------------


def test_rate_limit_gate_pauses_on_demand_work(database: Database, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "_ON_DEMAND_SUMMARY_SEMAPHORE", threading.BoundedSemaphore(3))
    gate = RateLimitGate()
    gate.note_rate_limit_hit(0.2)
    monkeypatch.setattr(dependencies, "_ON_DEMAND_RATE_LIMIT_GATE", gate)

    started_at: list[float] = []

    class _RecordingService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run_generation(self, order_id: str, as_of_date: date, prompt_version: str) -> None:
            started_at.append(time.monotonic())

    monkeypatch.setattr(dependencies, "FineProjectionSummaryService", _RecordingService)

    run_job = _make_runner(database, settings=Settings(on_demand_summary_acquire_timeout_seconds=5))

    begin = time.monotonic()
    run_job("ORD-GATE", date(2026, 8, 9), PROMPT_VERSION, None)

    assert started_at, "generation must still run once the gate clears"
    assert started_at[0] - begin >= 0.15


def test_rate_limited_failure_updates_the_shared_gate(database: Database, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "_ON_DEMAND_SUMMARY_SEMAPHORE", threading.BoundedSemaphore(3))
    gate = RateLimitGate()
    monkeypatch.setattr(dependencies, "_ON_DEMAND_RATE_LIMIT_GATE", gate)

    class _RateLimitedService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run_generation(self, order_id: str, as_of_date: date, prompt_version: str) -> None:
            raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(dependencies, "FineProjectionSummaryService", _RateLimitedService)

    run_job = _make_runner(
        database,
        settings=Settings(on_demand_summary_acquire_timeout_seconds=2, llm_rate_limit_backoff_seconds=1),
    )
    run_job("ORD-429", date(2026, 8, 9), PROMPT_VERSION, None)

    assert gate.rate_limit_hits == 1
