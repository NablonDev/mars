"""Tests for app.workers.fine_mitigation::sweep_stranded_pending_mitigation_summaries
-- full mirror of tests/unit/workers/test_worker_fine_projection.py for the
mitigation-summary feature."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.db.session import Database
from app.repositories.fine_mitigation.summary import FineMitigationSummaryRepository
from app.repositories.job_queue import JobQueueRepository
from app.workers.fine_mitigation import sweep_stranded_pending_mitigation_summaries

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
    FineMitigationSummaryRepository(db_session).create_pending(
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
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 1
    assert len(dispatcher.dispatched) == 1

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)
    assert len(items) == 1
    assert items[0]["order_id"] == "ORD-STRANDED"
    assert items[0]["projection_date"] == _TODAY
    assert items[0]["task_type"] == "MITIGATION_SUMMARY_REGEN"
    assert items[0]["status"] == "PENDING"
    assert items[0]["id"] in dispatcher.dispatched


def test_sweep_leaves_a_pending_row_alone_when_a_job_item_already_covers_it(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-COVERED", _TODAY)

    repo = JobQueueRepository(db_session)
    run = repo.create_run(run_type="ON_DEMAND", projection_date=_TODAY)
    repo.enqueue(run["id"], "ORD-COVERED", _TODAY, "MITIGATION_SUMMARY_REGEN", max_attempts=5)
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_a_row_outside_the_day_window_alone(database: Database, db_session):
    stale_date = _TODAY - timedelta(days=10)
    _make_pending_summary(db_session, "ORD-TOO-OLD", stale_date)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_ready_and_failed_rows_alone(database: Database, db_session):
    repo = FineMitigationSummaryRepository(db_session)
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
        summary="Accepting the fine is best.",
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
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_is_idempotent_on_a_second_run(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-TWICE", _TODAY)

    dispatcher = _RecordingDispatcher()
    settings = Settings(summary_pending_sweep_days=3)

    first = sweep_stranded_pending_mitigation_summaries(dispatcher, database, settings, today=_TODAY)
    second = sweep_stranded_pending_mitigation_summaries(dispatcher, database, settings, today=_TODAY)

    assert first.recovered_count == 1
    assert second.recovered_count == 0
    assert len(dispatcher.dispatched) == 1


def test_sweep_finds_nothing_when_there_are_no_pending_rows(database: Database):
    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert result.job_run_id is None
    assert dispatcher.dispatched == []


def test_sweep_does_not_recover_stranded_projection_summary_rows(database: Database, db_session):
    """The two sweeps are independent: a stranded projection_summary
    row must not be picked up by the mitigation-summary sweep."""
    from app.repositories.fine_projection.summary import FineProjectionSummaryRepository

    FineProjectionSummaryRepository(db_session).create_pending(
        order_id="ORD-PROJ-ONLY",
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        context_hash="deadbeef",
    )
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []
