"""Tests for app/workers/loop.py.

Mocks the `JobSource` Protocol entirely (`FakeJobSource` below) -- no SQL,
no Azure, no live LLM call anywhere in this file. `execute_job_fn` is
always a small in-test fake standing in for `app.workers.dispatch.execute_job`,
per the injectable seam `process_jobs`/`_process_job` expose specifically
for this purpose.

`enqueue_daily_run` moved to `app/workers/fine_projection.py` (it's
fine_projection-specific, not domain-agnostic loop machinery) -- its tests
moved with it, to `tests/unit/workers/test_worker_fine_projection.py`.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.config import Settings
from app.core.exceptions import (
    ExternalServiceError,
    InvalidAsOfDateError,
    NoActiveRulesError,
    NoProjectionExistsError,
    OrderNotFoundError,
    ToolLoopExhaustedError,
)
from app.core.rate_limit import RateLimitGate, looks_like_rate_limit
from app.queue.types import ClaimedJob
from app.workers.loop import (
    Classification,
    WorkerLoopSummary,
    _check_pool_headroom,
    _process_job,
    classify_failure,
    compute_backoff_seconds,
    process_jobs,
)


@pytest.fixture(autouse=True)
def _ensure_worker_loop_logger_enabled():
    """Guards against an external landmine, not a bug in this module:
    `alembic/env.py` (off-limits to edit here) calls `logging.config
    .fileConfig(...)` with its default `disable_existing_loggers=True`
    whenever a migration actually runs in-process in this same pytest
    session (`tests/test_migration_parity.py` does exactly that). That
    permanently sets `.disabled = True` on every logger that already
    existed at that point -- including this module's own
    `app.workers.loop` logger (created at import time, well before any
    test runs) -- which silently breaks every `caplog`-based assertion
    below for the rest of the session, regardless of run order otherwise.
    Scoped to just this file's logger and restored afterward so it can't
    mask the same landmine mattering somewhere else."""
    logger = logging.getLogger("app.workers.loop")
    previous = logger.disabled
    logger.disabled = False
    yield
    logger.disabled = previous


# ---------------------------------------------------------------------
# Fakes for the JobSource / JobDispatcher Protocols (app/queue/interfaces.py)
# ---------------------------------------------------------------------


@dataclass
class _Call:
    method: str
    job: ClaimedJob | None
    kwargs: dict = field(default_factory=dict)


class FakeJobSource:
    """Records every call it receives; `claim_batch` hands out pre-programmed
    batches (one list per call, empty list once exhausted)."""

    def __init__(
        self,
        *,
        batches: list[list[ClaimedJob]] | None = None,
        heartbeat_ok: bool = True,
        reclaim_stale_return: int = 0,
        on_claim_batch=None,
    ) -> None:
        self._batches = list(batches or [])
        self.heartbeat_ok = heartbeat_ok
        self._reclaim_stale_return = reclaim_stale_return
        self._on_claim_batch = on_claim_batch
        self.calls: list[_Call] = []
        # Deliberately a separate counter from `len(self.calls)` -- other
        # methods (reclaim_stale, ack, ...) also append to `self.calls`,
        # so indexing `on_claim_batch` off that list's length would shift
        # depending on which other calls happened first (e.g.
        # `process_jobs`'s own `reclaim_stale_first` sweep).
        self.claim_batch_call_count = 0
        self.lock = threading.Lock()

    def claim_batch(self, worker_id: str, limit: int) -> list[ClaimedJob]:
        with self.lock:
            call_index = self.claim_batch_call_count
            self.claim_batch_call_count += 1
            if self._on_claim_batch is not None:
                self._on_claim_batch(call_index)
            batch = self._batches.pop(0) if self._batches else []
            self.calls.append(_Call("claim_batch", None, {"worker_id": worker_id, "limit": limit}))
            return batch

    def heartbeat(self, job: ClaimedJob, worker_id: str) -> bool:
        with self.lock:
            self.calls.append(_Call("heartbeat", job, {"worker_id": worker_id}))
            return self.heartbeat_ok

    def ack(self, job: ClaimedJob, worker_id: str) -> None:
        with self.lock:
            self.calls.append(_Call("ack", job, {"worker_id": worker_id}))

    def nack(
        self, job: ClaimedJob, worker_id: str, *, error: str, error_code: str, retry_in_seconds: int
    ) -> None:
        with self.lock:
            self.calls.append(
                _Call(
                    "nack",
                    job,
                    {
                        "worker_id": worker_id,
                        "error": error,
                        "error_code": error_code,
                        "retry_in_seconds": retry_in_seconds,
                    },
                )
            )

    def dead_letter(self, job: ClaimedJob, worker_id: str, *, error: str, error_code: str) -> None:
        with self.lock:
            self.calls.append(
                _Call("dead_letter", job, {"worker_id": worker_id, "error": error, "error_code": error_code})
            )

    def release(self, job: ClaimedJob, worker_id: str) -> None:
        with self.lock:
            self.calls.append(_Call("release", job, {"worker_id": worker_id}))

    def reclaim_stale(self, visibility_timeout_seconds: int) -> int:
        with self.lock:
            self.calls.append(
                _Call("reclaim_stale", None, {"visibility_timeout_seconds": visibility_timeout_seconds})
            )
            return self._reclaim_stale_return

    def calls_for(self, method: str) -> list[_Call]:
        return [c for c in self.calls if c.method == method]


def _make_job(
    order_id: str = "ORD-1",
    projection_date: date = date(2026, 8, 13),
    task_type: str = "ORDER_RUN",
    *,
    attempt_count: int = 1,
    max_attempts: int = 5,
) -> ClaimedJob:
    return ClaimedJob(
        job_item_id=uuid4(),
        job_run_id=uuid4(),
        order_id=order_id,
        projection_date=projection_date,
        task_type=task_type,
        stacking_mode_override=None,
        force_regenerate_summary=False,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    )


def _fast_settings(**overrides):
    """Settings tuned so time-based loop behavior (polling, idle backoff)
    is fast enough for tests, overridable per-test."""
    defaults = {
        "job_queue_worker_concurrency": 3,
        "job_queue_batch_size": 10,
        "job_queue_poll_interval_seconds": 1,
        "job_queue_idle_poll_max_seconds": 4,
        "job_queue_item_deadline_seconds": 5,
        "job_queue_backoff_base_seconds": 10,
        "job_queue_backoff_cap_seconds": 1000,
        "job_queue_backoff_jitter_seconds": 0,
        "llm_rate_limit_backoff_seconds": 1,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------
# classify_failure -- the table
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        (OrderNotFoundError("ORD-1"), Classification.DEAD_LETTER),
        (NoActiveRulesError("no active rules"), Classification.DEAD_LETTER),
        (NoProjectionExistsError("no projection"), Classification.DEAD_LETTER),
        (InvalidAsOfDateError("bad date"), Classification.DEAD_LETTER),
        (ValueError("bad rule data"), Classification.DEAD_LETTER),
        (ExternalServiceError("upstream down"), Classification.NACK),
        (ToolLoopExhaustedError("exhausted", domain="projection"), Classification.NACK),
        (OperationalError("SELECT 1", {}, Exception("connection reset")), Classification.NACK),
        (DBAPIError("SELECT 1", {}, Exception("db gone away")), Classification.NACK),
        (RuntimeError("totally unexpected bug"), Classification.NACK),
    ],
)
def test_classify_failure_table(exc, expected):
    assert classify_failure(exc) is expected


# ---------------------------------------------------------------------
# compute_backoff_seconds
# ---------------------------------------------------------------------


def test_compute_backoff_seconds_grows_exponentially_and_caps():
    kwargs = {"base": 10, "cap": 1000, "jitter": 0}
    assert compute_backoff_seconds(1, **kwargs) == 10
    assert compute_backoff_seconds(2, **kwargs) == 20
    assert compute_backoff_seconds(3, **kwargs) == 40
    assert compute_backoff_seconds(4, **kwargs) == 80
    assert compute_backoff_seconds(10, **kwargs) == 1000  # capped


def test_compute_backoff_seconds_adds_jitter_within_bounds():
    values = {compute_backoff_seconds(1, base=10, cap=1000, jitter=5) for _ in range(50)}
    assert all(10 <= v <= 15 for v in values)
    assert len(values) > 1  # actually random, not a constant


# ---------------------------------------------------------------------
# _looks_like_rate_limit
# ---------------------------------------------------------------------


def test_looks_like_rate_limit_matches_status_code_and_phrases():
    assert looks_like_rate_limit(RuntimeError("429 Too Many Requests")) is True
    assert looks_like_rate_limit(RuntimeError("Rate limit exceeded, back off")) is True
    assert looks_like_rate_limit(RuntimeError("quota exceeded for this deployment")) is True


def test_looks_like_rate_limit_walks_the_cause_chain():
    try:
        try:
            raise RuntimeError("429 Too Many Requests")
        except RuntimeError as inner:
            raise ToolLoopExhaustedError("upstream failed", domain="projection") from inner
    except ToolLoopExhaustedError as outer:
        assert looks_like_rate_limit(outer) is True


def test_looks_like_rate_limit_false_for_unrelated_errors():
    assert looks_like_rate_limit(RuntimeError("connection reset by peer")) is False
    assert looks_like_rate_limit(OrderNotFoundError("ORD-1")) is False


# ---------------------------------------------------------------------
# _RateLimitGate
# ---------------------------------------------------------------------


def test_rate_limit_gate_pauses_until_backoff_elapses():
    gate = RateLimitGate()
    shutdown = threading.Event()

    gate.note_rate_limit_hit(0.15)
    start = time.monotonic()
    gate.wait_if_paused(shutdown)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.1
    assert gate.rate_limit_hits == 1


def test_rate_limit_gate_second_hit_extends_not_shortens_the_pause():
    gate = RateLimitGate()
    gate.note_rate_limit_hit(0.05)
    gate.note_rate_limit_hit(0.3)  # a later, longer pause must win
    shutdown = threading.Event()

    start = time.monotonic()
    gate.wait_if_paused(shutdown)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.25
    assert gate.rate_limit_hits == 2


def test_rate_limit_gate_returns_immediately_once_shutdown_is_set():
    gate = RateLimitGate()
    gate.note_rate_limit_hit(30)
    shutdown = threading.Event()
    shutdown.set()

    start = time.monotonic()
    gate.wait_if_paused(shutdown)
    assert time.monotonic() - start < 1.0


def test_process_job_recognizes_rate_limit_failure_and_updates_the_shared_gate():
    """`llm_rate_limit_backoff_seconds` is an integer Settings field (real
    production config), so this only proves detection + the gate update
    -- it does not wait the real pause out (see the next test for that,
    primed directly on the gate to keep it sub-second)."""
    gate = RateLimitGate()
    job_source = FakeJobSource()
    settings = _fast_settings(llm_rate_limit_backoff_seconds=1)

    def _rate_limited(job, database, settings, llm, heartbeat):
        raise ExternalServiceError("429 Too Many Requests from upstream")

    outcome = _process_job(
        _make_job("ORD-1"),
        job_source=job_source,
        database=None,
        settings=settings,
        llm=None,
        worker_id="w1",
        rate_limit_gate=gate,
        execute_job_fn=_rate_limited,
        startup_jitter_max_seconds=0,
        deadline_seconds=5,
        shutdown_event=threading.Event(),
    )

    assert outcome == "nacked"
    assert gate.rate_limit_hits == 1


def test_process_job_honors_a_rate_limit_pause_set_by_another_call():
    """Proves the gate is genuinely shared across calls (standing in for
    separate worker threads): priming it directly -- as
    `test_process_job_recognizes_rate_limit_failure_and_updates_the_shared_gate`
    just proved a real failure does -- must delay a completely unrelated
    job's `_process_job` call before it does any work at all."""
    gate = RateLimitGate()
    gate.note_rate_limit_hit(0.2)
    job_source = FakeJobSource()
    started_at: list[float] = []

    def _record_start(job, database, settings, llm, heartbeat):
        started_at.append(time.monotonic())

    begin = time.monotonic()
    outcome = _process_job(
        _make_job("ORD-2"),
        job_source=job_source,
        database=None,
        settings=_fast_settings(),
        llm=None,
        worker_id="w1",
        rate_limit_gate=gate,
        execute_job_fn=_record_start,
        startup_jitter_max_seconds=0,
        deadline_seconds=5,
        shutdown_event=threading.Event(),
    )

    assert outcome == "succeeded"
    assert started_at[0] - begin >= 0.15


# ---------------------------------------------------------------------
# _process_job -- dispatch, classification, deadline, heartbeat
# ---------------------------------------------------------------------


def _process(job, job_source, execute_job_fn, settings=None, shutdown=None):
    resolved_settings = settings or _fast_settings()
    return _process_job(
        job,
        job_source=job_source,
        database=None,
        settings=resolved_settings,
        llm=None,
        worker_id="w1",
        rate_limit_gate=RateLimitGate(),
        execute_job_fn=execute_job_fn,
        startup_jitter_max_seconds=0,
        deadline_seconds=resolved_settings.job_queue_item_deadline_seconds,
        shutdown_event=shutdown or threading.Event(),
    )


def test_process_job_dispatches_task_type_to_execute_job_fn_and_acks_on_success():
    job_source = FakeJobSource()
    seen_task_types = []

    def _execute(job, database, settings, llm, heartbeat):
        seen_task_types.append(job.task_type)

    job = _make_job(task_type="PROJECTION_SUMMARY_REGEN")
    outcome = _process(job, job_source, _execute)

    assert outcome == "succeeded"
    assert seen_task_types == ["PROJECTION_SUMMARY_REGEN"]
    ack_calls = job_source.calls_for("ack")
    assert len(ack_calls) == 1
    assert ack_calls[0].job is job
    assert not job_source.calls_for("nack")
    assert not job_source.calls_for("dead_letter")


def test_process_job_retryable_failure_nacks_with_growing_backoff():
    job_source = FakeJobSource()
    settings = _fast_settings(job_queue_backoff_base_seconds=10, job_queue_backoff_jitter_seconds=0)

    def _fail(job, database, settings, llm, heartbeat):
        raise ExternalServiceError("upstream 502")

    retry_delays = []
    for attempt in (1, 2, 3):
        job = _make_job(attempt_count=attempt)
        _process(job, job_source, _fail, settings=settings)
        retry_delays.append(job_source.calls_for("nack")[-1].kwargs["retry_in_seconds"])

    assert retry_delays == [10, 20, 40]
    assert not job_source.calls_for("dead_letter")


def test_process_job_non_retryable_failure_kills_on_first_occurrence():
    job_source = FakeJobSource()

    def _fail(job, database, settings, llm, heartbeat):
        raise OrderNotFoundError(job.order_id)

    job = _make_job(attempt_count=1, max_attempts=5)
    outcome = _process(job, job_source, _fail)

    assert outcome == "dead_lettered"
    kill_calls = job_source.calls_for("dead_letter")
    assert len(kill_calls) == 1
    assert kill_calls[0].kwargs["error_code"] == "ORDER_NOT_FOUND"
    assert not job_source.calls_for("nack")


def test_process_job_unclassified_exception_is_retryable_but_bounded(caplog):
    job_source = FakeJobSource()

    def _fail(job, database, settings, llm, heartbeat):
        raise RuntimeError("a genuinely unexpected bug")

    with caplog.at_level(logging.ERROR, logger="app.workers.loop"):
        not_exhausted = _make_job(attempt_count=1, max_attempts=5)
        outcome_not_exhausted = _process(not_exhausted, job_source, _fail)

        exhausted = _make_job(attempt_count=5, max_attempts=5)
        outcome_exhausted = _process(exhausted, job_source, _fail)

    # Unclassified is always NACK (never DEAD_LETTER) -- but the loop still
    # predicts whether *this* nack will exhaust max_attempts, purely for
    # the summary counters (JobSource.nack itself returns nothing).
    assert outcome_not_exhausted == "nacked"
    assert outcome_exhausted == "nacked_dead"
    assert len(job_source.calls_for("nack")) == 2
    assert not job_source.calls_for("dead_letter")
    assert any("unclassified exception" in r.message for r in caplog.records)


def test_process_job_deadline_breach_is_retryable():
    job_source = FakeJobSource()
    settings = _fast_settings(job_queue_item_deadline_seconds=1)

    def _slow(job, database, settings, llm, heartbeat):
        time.sleep(5)  # daemon thread; the process-level deadline is 0.05s below

    job = _make_job()
    outcome = _process_job(
        job,
        job_source=job_source,
        database=None,
        settings=settings,
        llm=None,
        worker_id="w1",
        rate_limit_gate=RateLimitGate(),
        execute_job_fn=_slow,
        startup_jitter_max_seconds=0,
        deadline_seconds=0.05,
        shutdown_event=threading.Event(),
    )

    assert outcome == "nacked"
    nack_calls = job_source.calls_for("nack")
    assert len(nack_calls) == 1
    assert nack_calls[0].kwargs["error_code"] == "ITEM_DEADLINE_EXCEEDED"


def test_process_job_heartbeat_returning_false_abandons_the_item():
    """When JobSource.heartbeat reports lost ownership, the item must be
    abandoned -- no ack, no nack, no dead_letter -- rather than settled as if it
    were still this worker's to settle."""
    job_source = FakeJobSource(heartbeat_ok=False)

    def _calls_heartbeat_then_would_succeed(job, database, settings, llm, heartbeat):
        heartbeat()  # raises _OwnershipLostError; never reaches the line below
        raise AssertionError("must not reach here once ownership is lost")

    job = _make_job()
    outcome = _process(job, job_source, _calls_heartbeat_then_would_succeed)

    assert outcome == "abandoned"
    assert not job_source.calls_for("ack")
    assert not job_source.calls_for("nack")
    assert not job_source.calls_for("dead_letter")
    assert len(job_source.calls_for("heartbeat")) == 1


# ---------------------------------------------------------------------
# _check_pool_headroom
# ---------------------------------------------------------------------


def test_check_pool_headroom_warns_when_concurrency_exceeds_pool_capacity(caplog):
    settings = Settings(job_queue_worker_concurrency=20, db_pool_size=5, db_max_overflow=10)

    with caplog.at_level(logging.WARNING, logger="app.workers.loop"):
        _check_pool_headroom(settings)

    assert any("exceeds DB capacity" in r.message for r in caplog.records)


def test_check_pool_headroom_silent_when_capacity_is_sufficient(caplog):
    settings = Settings(job_queue_worker_concurrency=5, db_pool_size=5, db_max_overflow=10)

    with caplog.at_level(logging.WARNING, logger="app.workers.loop"):
        _check_pool_headroom(settings)

    assert not any("exceeds DB capacity" in r.message for r in caplog.records)


def test_process_jobs_logs_pool_headroom_warning_at_startup(caplog):
    job_source = FakeJobSource()
    settings = _fast_settings(job_queue_worker_concurrency=20, db_pool_size=5, db_max_overflow=10)

    with caplog.at_level(logging.WARNING, logger="app.workers.loop"):
        summary = process_jobs(
            job_source,
            None,
            settings,
            None,
            mode="drain",
            execute_job_fn=lambda *a, **k: None,
            startup_jitter_max_seconds=0,
            install_signal_handler=False,
        )

    assert isinstance(summary, WorkerLoopSummary)
    assert any("exceeds DB capacity" in r.message for r in caplog.records)


# ---------------------------------------------------------------------
# process_jobs -- idle backoff growth/reset
# ---------------------------------------------------------------------


class _RecordingEvent(threading.Event):
    """Records every `timeout` a caller asks `.wait()` for, but actually
    blocks for at most a tiny fraction of it -- lets a test observe the
    real idle-backoff values `process_jobs` computes without the test
    itself taking as long as those backoffs would in production."""

    def __init__(self) -> None:
        super().__init__()
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:  # type: ignore[override]
        self.wait_timeouts.append(timeout)
        capped = min(timeout, 0.01) if timeout is not None else None
        return super().wait(timeout=capped)


def test_process_jobs_idle_backoff_grows_then_resets_on_a_new_batch():
    event = _RecordingEvent()
    call_count = {"n": 0}

    def _on_claim_batch(call_index: int) -> None:
        call_count["n"] = call_index + 1
        # 6th call (index 5): stop the loop right after this poll's wait.
        if call_count["n"] >= 6:
            event.set()

    # Calls 1-4 empty, call 5 returns one job (backoff should reset), call 6 empty again.
    job = _make_job("ORD-RESET")
    job_source = FakeJobSource(batches=[[], [], [], [], [job], []], on_claim_batch=_on_claim_batch)
    settings = _fast_settings(job_queue_poll_interval_seconds=1, job_queue_idle_poll_max_seconds=10)

    summary = process_jobs(
        job_source,
        None,
        settings,
        None,
        mode="service",
        execute_job_fn=lambda *a, **k: None,
        startup_jitter_max_seconds=0,
        shutdown_event=event,
        install_signal_handler=False,
    )

    # 4 empty polls growing 1 -> 2 -> 4 -> 8, then reset to 1 on the batch,
    # then one more empty poll waiting at the reset value (1) again.
    assert event.wait_timeouts == [1.0, 2.0, 4.0, 8.0, 1.0]
    assert summary.succeeded == 1


# ---------------------------------------------------------------------
# process_jobs -- graceful shutdown release
# ---------------------------------------------------------------------


def test_process_jobs_shutdown_releases_in_flight_items_without_consuming_an_attempt():
    job = _make_job("ORD-SLOW", attempt_count=1, max_attempts=5)
    job_source = FakeJobSource(batches=[[job]])
    # `threading.Event.wait(timeout=...)` returns as soon as the event is
    # set, not only once the full timeout elapses, so the (integer-only,
    # real production) poll-interval default here doesn't slow this test
    # down -- shutdown_event.set() below still short-circuits promptly.
    settings = _fast_settings()
    shutdown_event = threading.Event()

    def _slow_execute(job, database, settings, llm, heartbeat):
        time.sleep(0.3)

    result = {}

    def _run():
        result["summary"] = process_jobs(
            job_source,
            None,
            settings,
            None,
            mode="service",
            execute_job_fn=_slow_execute,
            startup_jitter_max_seconds=0,
            shutdown_grace_seconds=0.05,
            shutdown_event=shutdown_event,
            install_signal_handler=False,
        )

    thread = threading.Thread(target=_run)
    thread.start()
    time.sleep(0.05)  # let the job get claimed and start running
    shutdown_event.set()
    thread.join(timeout=5)

    assert not thread.is_alive(), "process_jobs must return promptly once its grace window elapses"
    summary = result["summary"]
    assert summary.released == 1
    assert summary.succeeded == 0

    release_calls = job_source.calls_for("release")
    assert len(release_calls) == 1
    assert release_calls[0].job.job_item_id == job.job_item_id
    # Not settled any other way -- the row must come back exactly once,
    # via release, not also (or instead) via ack/nack/dead_letter.
    assert not job_source.calls_for("ack")
    assert not job_source.calls_for("nack")
    assert not job_source.calls_for("dead_letter")


def test_process_jobs_drain_mode_exits_once_queue_is_empty():
    job_source = FakeJobSource(batches=[[_make_job("ORD-ONE")], []])
    settings = _fast_settings()
    processed = []

    summary = process_jobs(
        job_source,
        None,
        settings,
        None,
        mode="drain",
        execute_job_fn=lambda job, *a, **k: processed.append(job.order_id),
        startup_jitter_max_seconds=0,
        install_signal_handler=False,
    )

    assert processed == ["ORD-ONE"]
    assert summary.succeeded == 1
    assert job_source.calls_for("ack")


def test_process_jobs_reclaims_stale_before_claiming_new_work():
    job_source = FakeJobSource(reclaim_stale_return=3)
    settings = _fast_settings()

    process_jobs(
        job_source,
        None,
        settings,
        None,
        mode="drain",
        execute_job_fn=lambda *a, **k: None,
        startup_jitter_max_seconds=0,
        install_signal_handler=False,
    )

    assert len(job_source.calls_for("reclaim_stale")) == 1
