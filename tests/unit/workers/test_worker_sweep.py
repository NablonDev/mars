"""Tests for app.workers.sweep -- the recovery sweep that closes the
fine-summary two-commit durability gap between
`FineSummaryService.get_or_schedule`'s PENDING ledger write and the
caller's separate `job_item` write (see that module's docstring).

Run against the same in-memory SQLite `database`/`db_session` fixtures as
the rest of the queue-layer tests (see conftest.py) -- `db_session` sets
up the "stranded" state directly via the repositories, and
`sweep_stranded_pending_summaries` is exercised end to end against the
shared `database` fixture, same pattern as `app.workers.loop
.enqueue_daily_run`'s own tests.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.db.session import Database
from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.job_queue import JobQueueRepository
from app.workers.sweep import sweep_stranded_pending_summaries

_TODAY = date(2026, 8, 14)
_AGENT_ID = uuid4()
_PROMPT_VERSION = "v-test"


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[UUID] = []

    def dispatch(self, job_item_id: UUID, *, delay_seconds: int = 0) -> None:
        self.dispatched.append(job_item_id)

    def close(self) -> None:
        pass


def _make_pending_summary(db_session, order_id: str, as_of_date: date) -> None:
    FineSummaryRepository(db_session).create_pending(
        order_id=order_id,
        as_of_date=as_of_date,
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        context_hash="deadbeef",
    )
    db_session.commit()


def test_sweep_recovers_a_stranded_pending_row_with_no_job_item(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-STRANDED", _TODAY)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 1
    assert len(dispatcher.dispatched) == 1

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)
    assert len(items) == 1
    assert items[0]["order_id"] == "ORD-STRANDED"
    assert items[0]["projection_date"] == _TODAY
    assert items[0]["task_type"] == "SUMMARY_REGEN"
    assert items[0]["status"] == "PENDING"
    assert items[0]["id"] in dispatcher.dispatched


def test_sweep_leaves_a_pending_row_alone_when_a_job_item_already_covers_it(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-COVERED", _TODAY)

    repo = JobQueueRepository(db_session)
    run = repo.create_run(run_type="ON_DEMAND", projection_date=_TODAY)
    repo.enqueue(run["id"], "ORD-COVERED", _TODAY, "SUMMARY_REGEN", max_attempts=5)
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_a_pending_row_alone_even_when_its_job_item_is_terminal(database: Database, db_session):
    """A DEAD or SUCCEEDED job_item still counts as "the queue already
    knows about this row" -- the sweep's job is to catch rows the queue
    never learned about at all, not to re-drive one whose job_item the
    queue already resolved one way or another."""
    _make_pending_summary(db_session, "ORD-DEAD-COVERED", _TODAY)

    repo = JobQueueRepository(db_session)
    run = repo.create_run(run_type="ON_DEMAND", projection_date=_TODAY)
    item = repo.enqueue(run["id"], "ORD-DEAD-COVERED", _TODAY, "SUMMARY_REGEN", max_attempts=5)
    repo.claim_batch("worker-1", limit=1, job_item_ids=[item["id"]])
    repo.mark_dead(item["id"], "worker-1", "boom", "SOME_FAILURE")
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_a_row_outside_the_day_window_alone(database: Database, db_session):
    stale_date = _TODAY - timedelta(days=10)
    _make_pending_summary(db_session, "ORD-TOO-OLD", stale_date)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_ready_and_failed_rows_alone(database: Database, db_session):
    repo = FineSummaryRepository(db_session)
    repo.create_pending(
        order_id="ORD-READY",
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        context_hash="hash1",
    )
    repo.mark_ready(
        order_id="ORD-READY",
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        model_name="gpt-test",
        summary="All clear.",
    )
    repo.create_pending(
        order_id="ORD-FAILED",
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        context_hash="hash2",
    )
    repo.mark_failed(
        order_id="ORD-FAILED",
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        error_message="boom",
    )
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_is_idempotent_on_a_second_run(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-TWICE", _TODAY)

    dispatcher = _RecordingDispatcher()
    settings = Settings(summary_pending_sweep_days=3)

    first = sweep_stranded_pending_summaries(dispatcher, database, settings, today=_TODAY)
    second = sweep_stranded_pending_summaries(dispatcher, database, settings, today=_TODAY)

    assert first.recovered_count == 1
    assert second.recovered_count == 0
    assert len(dispatcher.dispatched) == 1


def test_sweep_recovers_multiple_stranded_rows_in_one_pass(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-MULTI-1", _TODAY)
    _make_pending_summary(db_session, "ORD-MULTI-2", _TODAY)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 2
    assert len(dispatcher.dispatched) == 2


def test_sweep_finds_nothing_when_there_are_no_pending_rows(database: Database):
    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert result.job_run_id is None
    assert dispatcher.dispatched == []
