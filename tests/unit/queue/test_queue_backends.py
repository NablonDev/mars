"""Parity tests for the pluggable job-dispatch seam (`app/queue`).

One shared behavioral suite, parameterized over both backends
(`PostgresJobQueue`, `ServiceBusJobQueue`), so these tests prove the
`JOB_QUEUE_BACKEND` flag is real rather than aspirational -- every
parity case below runs against both. Service-Bus-only behavior (the
dedup/redelivery-safety property the whole design rests on, and the
"already terminal row" settle case) gets its own tests further down.

Runs entirely against the SQLite in-memory `database` fixture (see
tests/conftest.py) plus a small in-memory fake Service Bus client
defined in this file -- no live Azure, no network. Per
`JobQueueRepository`'s own module docstring, its SQLite fallbacks do NOT
provide real cross-connection concurrency guarantees; nothing here
relies on that -- these tests are single-threaded and exercise state-
machine/dispatch-plumbing correctness, not concurrent-claim safety (that
is `tests/integration/test_job_queue_postgres.py`'s job, gated on a
reachable Postgres exactly as it already is).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings
from app.core.exceptions import ValidationError
from app.db.session import Database
from app.models import JobItem
from app.queue.factory import build_job_queue
from app.queue.interfaces import JobDispatcher, JobSource
from app.queue.postgres import PostgresJobQueue
from app.queue.service_bus import ServiceBusJobQueue
from app.queue.types import ClaimedJob
from app.repositories.job_queue import JobQueueRepository

ORDER_ID = "ORD-QUEUE-TEST"
FAKE_NAMESPACE = "fake-namespace.servicebus.windows.net"


# ---------------------------------------------------------------------
# In-memory fake Service Bus -- records sent/completed/abandoned/dead-
# lettered messages and hands them back out on receive. No network.
# ---------------------------------------------------------------------


@dataclass
class _FakeMessage:
    body: str
    lock_lost: bool = False


class _ConcurrencyProbe:
    """Fails loudly if two threads are ever inside a guarded block at the
    same time -- the whole point of this fixture. It exists to make
    `ServiceBusJobQueue`'s lock (see `app/queue/service_bus.py`) provably
    load-bearing: without it, `_FakeSender`/`_FakeReceiver` are plain
    Python objects with no thread-safety concern of their own, so a
    missing lock in production code would never fail a test built only on
    them -- this probe is what turns that missing lock into a
    deterministic (not flaky) test failure.

    Deliberately NOT guarded by its own lock: `_in_use` is a plain bool,
    checked and set without synchronization, with a short `time.sleep`
    between the two so a second thread has a real window to land on the
    same check while the first is still "inside." A properly-locked
    caller (i.e. every real SDK call site in `ServiceBusJobQueue`) can
    never have two threads reach this at once in the first place, so the
    probe's own lack of synchronization is never exercised there.
    """

    def __init__(self) -> None:
        self._in_use = False

    def __enter__(self) -> None:
        if self._in_use:
            raise AssertionError(
                "concurrent Service Bus SDK call detected -- two threads were inside "
                "a guarded call at once, which means the caller's lock is not "
                "actually serializing access"
            )
        self._in_use = True
        time.sleep(0.01)  # widen the race window so concurrent calls reliably overlap

    def __exit__(self, *exc_info: object) -> None:
        self._in_use = False


class _FakeServiceBusQueue:
    """Shared mutable state a fake sender/receiver pair operates on --
    stands in for a real Service Bus queue's server-side state.

    `probe` is shared by both the fake sender and the fake receiver,
    mirroring `ServiceBusJobQueue`'s single `self._lock` guarding every
    SDK call across both resources (see that class's docstring for why
    one lock, not one per resource)."""

    def __init__(self) -> None:
        self.pending: list[_FakeMessage] = []
        self.completed: list[_FakeMessage] = []
        self.abandoned: list[_FakeMessage] = []
        self.dead_lettered: list[_FakeMessage] = []
        self.scheduled: list[tuple[_FakeMessage, datetime]] = []
        self.closed: list[str] = []
        self.probe = _ConcurrencyProbe()


class _FakeSender:
    def __init__(self, queue: _FakeServiceBusQueue) -> None:
        self._queue = queue

    def send_messages(self, message: _FakeMessage) -> None:
        with self._queue.probe:
            self._queue.pending.append(message)

    def schedule_messages(self, message: _FakeMessage, scheduled_time_utc: datetime) -> None:
        # No real scheduler in the fake: delay timing itself is proven at
        # the `available_at`/Postgres level by the existing
        # JobQueueRepository test suite, not here -- this fake only needs
        # to prove a scheduled message *was requested*, and that it
        # eventually becomes receivable.
        with self._queue.probe:
            self._queue.scheduled.append((message, scheduled_time_utc))
            self._queue.pending.append(message)

    def close(self) -> None:
        with self._queue.probe:
            self._queue.closed.append("sender")


class _FakeReceiver:
    def __init__(self, queue: _FakeServiceBusQueue) -> None:
        self._queue = queue

    def receive_messages(self, *, max_message_count: int) -> list[_FakeMessage]:
        with self._queue.probe:
            batch = self._queue.pending[:max_message_count]
            self._queue.pending = self._queue.pending[max_message_count:]
            return batch

    def complete_message(self, message: _FakeMessage) -> None:
        with self._queue.probe:
            self._queue.completed.append(message)

    def abandon_message(self, message: _FakeMessage) -> None:
        with self._queue.probe:
            self._queue.abandoned.append(message)
            self._queue.pending.append(message)  # immediately redeliverable

    def dead_letter_message(
        self, message: _FakeMessage, *, reason: str | None = None, error_description: str | None = None
    ) -> None:
        with self._queue.probe:
            self._queue.dead_lettered.append(message)

    def renew_message_lock(self, message: _FakeMessage) -> None:
        with self._queue.probe:
            if message.lock_lost:
                raise RuntimeError("simulated lock loss")

    def close(self) -> None:
        with self._queue.probe:
            self._queue.closed.append("receiver")


class _FakeServiceBusClient:
    def __init__(self, queue: _FakeServiceBusQueue) -> None:
        self._queue = queue

    def get_queue_sender(self, *, queue_name: str) -> _FakeSender:
        return _FakeSender(self._queue)

    def get_queue_receiver(
        self,
        *,
        queue_name: str,
        max_wait_time: float | None = None,
        receive_mode: str | None = None,
    ) -> _FakeReceiver:
        # `receive_mode` is accepted (and ignored) purely so this fake's
        # signature matches the real SDK call `ServiceBusJobQueue.__init__`
        # now makes explicitly (see `_RECEIVE_MODE_PEEK_LOCK` in
        # `app/queue/service_bus.py`) -- this fake has no concept of
        # message-visibility semantics to vary by mode.
        return _FakeReceiver(self._queue)

    def close(self) -> None:
        self._queue.closed.append("client")


def _fake_message_factory(body: str) -> _FakeMessage:
    return _FakeMessage(body=body)


def _make_service_bus_queue(database: Database, fake_bus: _FakeServiceBusQueue) -> ServiceBusJobQueue:
    settings = Settings(job_queue_service_bus_namespace=FAKE_NAMESPACE)
    return ServiceBusJobQueue(
        database,
        settings,
        client=_FakeServiceBusClient(fake_bus),
        message_factory=_fake_message_factory,
    )


# ---------------------------------------------------------------------
# Shared test plumbing
# ---------------------------------------------------------------------


def _enqueue_item(database: Database, *, max_attempts: int = 5) -> dict[str, Any]:
    with database.session() as session:
        repo = JobQueueRepository(session)
        run = repo.create_run(run_type="MANUAL_BATCH", projection_date=date(2026, 8, 13))
        item = repo.enqueue(run["id"], ORDER_ID, date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
        assert item is not None
        if max_attempts != 5:
            row = session.get(JobItem, item["id"])
            assert row is not None
            row.max_attempts = max_attempts
    return item


def _status(database: Database, job_item_id: UUID) -> str:
    with database.session() as session:
        row = session.get(JobItem, job_item_id)
        assert row is not None
        return row.status


@pytest.fixture(params=["postgres", "service_bus"])
def backend_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def fake_bus() -> _FakeServiceBusQueue:
    return _FakeServiceBusQueue()


@pytest.fixture
def queue(database: Database, backend_name: str, fake_bus: _FakeServiceBusQueue):
    if backend_name == "postgres":
        return PostgresJobQueue(database)
    return _make_service_bus_queue(database, fake_bus)


# ---------------------------------------------------------------------
# Parity cases -- identical behavior required of both backends.
# ---------------------------------------------------------------------


def test_dispatch_then_claim_batch_returns_the_item(database: Database, queue) -> None:
    item = _enqueue_item(database)

    queue.dispatch(item["id"])
    batch = queue.claim_batch("worker-1", limit=10)

    assert len(batch) == 1
    job = batch[0]
    assert job.job_item_id == item["id"]
    assert job.order_id == ORDER_ID
    assert job.attempt_count == 1


def test_ack_marks_succeeded(database: Database, queue) -> None:
    item = _enqueue_item(database)
    queue.dispatch(item["id"])
    job = queue.claim_batch("worker-1", limit=10)[0]

    queue.ack(job, "worker-1")

    assert _status(database, item["id"]) == "SUCCEEDED"


def test_nack_retries_then_dead_at_max_attempts(database: Database, queue) -> None:
    item = _enqueue_item(database, max_attempts=2)
    queue.dispatch(item["id"])
    job = queue.claim_batch("worker-1", limit=10)[0]
    assert job.attempt_count == 1

    queue.nack(job, "worker-1", error="transient", error_code="TIMEOUT", retry_in_seconds=0)
    assert _status(database, item["id"]) == "PENDING"

    job2 = queue.claim_batch("worker-1", limit=10)[0]
    assert job2.attempt_count == 2

    queue.nack(job2, "worker-1", error="transient", error_code="TIMEOUT", retry_in_seconds=0)
    assert _status(database, item["id"]) == "DEAD"


def test_dead_letter_is_terminal_immediately(database: Database, queue) -> None:
    item = _enqueue_item(database)
    queue.dispatch(item["id"])
    job = queue.claim_batch("worker-1", limit=10)[0]
    assert job.attempt_count == 1  # nowhere near max_attempts (default 5)

    queue.dead_letter(job, "worker-1", error="non-retryable", error_code="BAD_INPUT")

    assert _status(database, item["id"]) == "DEAD"


def test_release_does_not_consume_an_attempt(database: Database, queue) -> None:
    item = _enqueue_item(database)
    queue.dispatch(item["id"])
    job = queue.claim_batch("worker-1", limit=10)[0]
    assert job.attempt_count == 1

    queue.release(job, "worker-1")
    assert _status(database, item["id"]) == "PENDING"

    job2 = queue.claim_batch("worker-1", limit=10)[0]
    assert job2.attempt_count == 2  # only the claim itself increments


def test_heartbeat_returns_false_after_ownership_loss(database: Database, queue, backend_name: str) -> None:
    item = _enqueue_item(database)
    queue.dispatch(item["id"])
    job = queue.claim_batch("worker-1", limit=10)[0]
    assert queue.heartbeat(job, "worker-1") is True

    if backend_name == "postgres":
        with database.session() as session:
            row = session.get(JobItem, item["id"])
            assert row is not None
            row.locked_by = "worker-2"
    else:
        assert isinstance(job.receipt, _FakeMessage)
        job.receipt.lock_lost = True

    assert queue.heartbeat(job, "worker-1") is False


def test_reclaim_stale_recovers_an_abandoned_item(database: Database, queue) -> None:
    item = _enqueue_item(database)
    queue.dispatch(item["id"])
    queue.claim_batch("worker-1", limit=10)

    with database.session() as session:
        row = session.get(JobItem, item["id"])
        assert row is not None
        row.heartbeat_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)

    reset_count = queue.reclaim_stale(visibility_timeout_seconds=60)

    assert reset_count == 1
    assert _status(database, item["id"]) == "PENDING"


# ---------------------------------------------------------------------
# Service-Bus-specific: the redelivery-safety property.
# ---------------------------------------------------------------------


def test_duplicate_dispatch_of_the_same_job_item_results_in_exactly_one_execution(
    database: Database, fake_bus: _FakeServiceBusQueue
) -> None:
    """The property the entire redelivery-safety argument rests on."""
    queue = _make_service_bus_queue(database, fake_bus)
    item = _enqueue_item(database)

    queue.dispatch(item["id"])
    queue.dispatch(item["id"])  # duplicate dispatch of the same row

    executed: list[UUID] = []
    for _ in range(2):
        for job in queue.claim_batch("worker-1", limit=10):
            executed.append(job.job_item_id)
            queue.ack(job, "worker-1")

    assert executed == [item["id"]]
    assert _status(database, item["id"]) == "SUCCEEDED"
    # The duplicate copy is settled (completed), not left to redeliver
    # forever: one completion from the duplicate-message cleanup in
    # claim_batch, one from ack.
    assert len(fake_bus.completed) == 2
    assert fake_bus.pending == []


def test_message_for_an_already_terminal_row_gets_settled_not_redelivered(
    database: Database, fake_bus: _FakeServiceBusQueue
) -> None:
    queue = _make_service_bus_queue(database, fake_bus)
    item = _enqueue_item(database)

    with database.session() as session:
        row = session.get(JobItem, item["id"])
        assert row is not None
        row.status = "SUCCEEDED"  # e.g. already handled by a prior delivery

    queue.dispatch(item["id"])  # a stray/duplicate message for the finished row
    batch = queue.claim_batch("worker-1", limit=10)

    assert batch == []
    assert len(fake_bus.completed) == 1
    assert fake_bus.pending == []


def test_service_bus_missing_namespace_raises_actionable_error(database: Database) -> None:
    settings = Settings(job_queue_service_bus_namespace="")
    with pytest.raises(ValidationError, match="JOB_QUEUE_SERVICE_BUS_NAMESPACE"):
        ServiceBusJobQueue(database, settings)


def test_service_bus_missing_package_raises_actionable_error(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "azure.servicebus", None)
    monkeypatch.setitem(sys.modules, "azure.identity", None)

    settings = Settings(job_queue_service_bus_namespace=FAKE_NAMESPACE)
    with pytest.raises(ValidationError, match="azure-servicebus"):
        ServiceBusJobQueue(database, settings)


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------


def test_factory_builds_postgres_backend_by_default(database: Database) -> None:
    settings = Settings()
    dispatcher, source = build_job_queue(settings, database)

    assert isinstance(dispatcher, PostgresJobQueue)
    assert dispatcher is source


def test_settings_rejects_unknown_job_queue_backend_at_startup() -> None:
    with pytest.raises(PydanticValidationError):
        Settings(job_queue_backend="carrier_pigeon")


# ---------------------------------------------------------------------
# close() -- the resource-leak fix.
# ---------------------------------------------------------------------


def test_postgres_close_is_a_harmless_noop(database: Database) -> None:
    queue = PostgresJobQueue(database)

    queue.close()
    queue.close()  # idempotent -- must not raise


def test_service_bus_close_disposes_client_sender_and_receiver(
    database: Database, fake_bus: _FakeServiceBusQueue
) -> None:
    queue = _make_service_bus_queue(database, fake_bus)

    queue.close()

    assert set(fake_bus.closed) == {"client", "sender", "receiver"}


def test_service_bus_close_is_idempotent(database: Database, fake_bus: _FakeServiceBusQueue) -> None:
    queue = _make_service_bus_queue(database, fake_bus)

    queue.close()
    queue.close()

    # Each resource closed exactly once, not twice, on the second call.
    assert len(fake_bus.closed) == 3


def test_service_bus_close_tolerates_a_resource_that_raises(
    database: Database, fake_bus: _FakeServiceBusQueue
) -> None:
    queue = _make_service_bus_queue(database, fake_bus)

    def _explode() -> None:
        raise RuntimeError("already disconnected")

    queue._sender.close = _explode  # type: ignore[method-assign]

    queue.close()  # must not raise -- receiver/client still get closed

    assert "receiver" in fake_bus.closed
    assert "client" in fake_bus.closed


# ---------------------------------------------------------------------
# Concurrency -- the lock guarding every Service Bus SDK call.
#
# `app.workers.loop.process_jobs` executes claimed items on a
# `ThreadPoolExecutor`, and every worker thread then calls heartbeat/ack/
# nack/dead_letter/release on the SAME `ServiceBusJobQueue` instance -- i.e. the
# same shared `self._sender`/`self._receiver`. `_ConcurrencyProbe` (see
# the top of this file) fails the instant two threads are inside a
# guarded SDK call at the same time, which is exactly what happens if
# `ServiceBusJobQueue`'s lock is removed -- proven by actually removing
# it and re-running these two tests (see the delivery report for the
# verbatim before/after output).
# ---------------------------------------------------------------------


def _enqueue_dispatch_and_claim_many(
    database: Database, queue: ServiceBusJobQueue, *, count: int, order_prefix: str
) -> list[ClaimedJob]:
    for i in range(count):
        with database.session() as session:
            repo = JobQueueRepository(session)
            run = repo.create_run(run_type="MANUAL_BATCH", projection_date=date(2026, 8, 13))
            item = repo.enqueue(
                run["id"], f"{order_prefix}-{i}", date(2026, 8, 13), "ORDER_RUN", max_attempts=5
            )
            assert item is not None
        queue.dispatch(item["id"])
    return queue.claim_batch("worker-1", limit=count)


def test_service_bus_receiver_calls_are_serialized_across_worker_threads(
    database: Database, fake_bus: _FakeServiceBusQueue
) -> None:
    """Reproduces the real production shape of the bug: N worker threads
    (standing in for `process_jobs`'s `ThreadPoolExecutor`) each settle
    their own already-claimed item -- heartbeat then ack -- concurrently
    against the one shared `ServiceBusJobQueue`. Every thread hits the
    shared receiver; without the lock, `_ConcurrencyProbe` catches two
    threads inside `renew_message_lock`/`complete_message` at once."""
    queue = _make_service_bus_queue(database, fake_bus)
    jobs = _enqueue_dispatch_and_claim_many(database, queue, count=8, order_prefix="ORD-RACE")
    assert len(jobs) == 8

    errors: list[BaseException] = []

    def _worker(job: ClaimedJob) -> None:
        try:
            assert queue.heartbeat(job, "worker-1") is True
            queue.ack(job, "worker-1")
        except BaseException as exc:  # noqa: BLE001 -- captured so every thread still finishes
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(job,)) for job in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"race(s) detected: {errors!r}"
    assert len(fake_bus.completed) == 8


def test_service_bus_sender_calls_are_serialized_across_concurrent_dispatch(
    database: Database, fake_bus: _FakeServiceBusQueue
) -> None:
    """Covers the other resource the same lock guards: `nack`'s retry path
    calls `dispatch()` (the sender) from whichever worker thread hit the
    failure, so N threads calling `dispatch()` concurrently must never
    overlap inside `send_messages` either."""
    queue = _make_service_bus_queue(database, fake_bus)
    item_ids: list[UUID] = []
    for i in range(8):
        with database.session() as session:
            repo = JobQueueRepository(session)
            run = repo.create_run(run_type="MANUAL_BATCH", projection_date=date(2026, 8, 13))
            item = repo.enqueue(
                run["id"], f"ORD-DISPATCH-RACE-{i}", date(2026, 8, 13), "ORDER_RUN", max_attempts=5
            )
            assert item is not None
            item_ids.append(item["id"])

    errors: list[BaseException] = []

    def _worker(item_id: UUID) -> None:
        try:
            queue.dispatch(item_id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(item_id,)) for item_id in item_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"race(s) detected: {errors!r}"
    assert len(fake_bus.pending) == 8


# ---------------------------------------------------------------------
# Explicit Protocol inheritance -- definition-site check, not runtime
# enforcement.
#
# `PostgresJobQueue`/`ServiceBusJobQueue` now explicitly subclass
# `JobDispatcher`/`JobSource` (PEP 544 permits this), so mypy checks
# conformance at the class definition. But unlike an ABC, a `Protocol`'s
# methods are NOT abstract at runtime -- inheriting from one gives no
# runtime enforcement. A concrete class that forgot to override a
# Protocol method would silently inherit the Protocol's own `...` stub
# body and return `None` at call time instead of raising anywhere. These
# tests catch that gap directly: every public method the two Protocols
# declare must appear in the concrete class's OWN `__dict__`, not merely
# be reachable via inheritance from the Protocol.
# ---------------------------------------------------------------------


def _protocol_public_methods(protocol_cls: type) -> set[str]:
    """Public callables `protocol_cls` itself defines (excluding dunders
    and Protocol machinery, which all start with `_`)."""
    return {
        name
        for name in vars(protocol_cls)
        if not name.startswith("_") and callable(getattr(protocol_cls, name, None))
    }


_EXPECTED_QUEUE_PROTOCOL_METHODS = _protocol_public_methods(JobDispatcher) | _protocol_public_methods(
    JobSource
)


def test_expected_queue_protocol_methods_is_not_accidentally_empty() -> None:
    """Guards the guard: if a future typing change ever made
    `_protocol_public_methods` return nothing, the two tests below would
    trivially "pass" without checking anything."""
    assert _EXPECTED_QUEUE_PROTOCOL_METHODS == {
        "dispatch",
        "close",
        "claim_batch",
        "heartbeat",
        "ack",
        "nack",
        "dead_letter",
        "release",
        "reclaim_stale",
    }


def test_postgres_job_queue_defines_every_protocol_method_on_its_own_class() -> None:
    own_members = set(vars(PostgresJobQueue))
    missing = _EXPECTED_QUEUE_PROTOCOL_METHODS - own_members
    assert not missing, (
        f"PostgresJobQueue does not override {missing} -- it would silently inherit the "
        "Protocol's own stub body (returns None) instead of raising NotImplementedError."
    )


def test_service_bus_job_queue_defines_every_protocol_method_on_its_own_class() -> None:
    own_members = set(vars(ServiceBusJobQueue))
    missing = _EXPECTED_QUEUE_PROTOCOL_METHODS - own_members
    assert not missing, (
        f"ServiceBusJobQueue does not override {missing} -- it would silently inherit the "
        "Protocol's own stub body (returns None) instead of raising NotImplementedError."
    )
