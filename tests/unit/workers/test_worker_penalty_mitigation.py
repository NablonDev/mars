"""Tests for app.workers.penalty_mitigation::sweep_stranded_pending_mitigation_summaries
-- full mirror of tests/unit/workers/test_worker_penalty_projection.py for the
mitigation-summary feature.

Was `tests/unit/workers/test_worker_fine_mitigation.py` (`fine`/`fines` ->
`penalty`/`penalties` rename) -- rewritten against the Phase 2/3
`process`/`penalties` repositories, keyed by the UUID surrogate
`purchase_order_id` and the merged `penalty_summary` table's
`summary_type` discriminator.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.db.session import Database
from app.models.enums import SummaryType
from app.repositories.penalties.job_context import PenaltyJobItemContextRepository
from app.repositories.penalties.summary import PenaltySummaryRepository
from app.repositories.process.job_queue import JobQueueRepository
from app.workers.penalty_mitigation import sweep_stranded_pending_mitigation_summaries

_TODAY = date(2026, 8, 14)
_AGENT_ID = uuid4()


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[UUID] = []

    def dispatch(self, job_item_id: UUID, *, delay_seconds: int = 0) -> None:
        self.dispatched.append(job_item_id)

    def close(self) -> None:
        pass


def _make_pending_summary(db_session, purchase_order_id: UUID, as_of_date: date) -> None:
    PenaltySummaryRepository(db_session).create_pending(
        purchase_order_id=purchase_order_id,
        summary_type=SummaryType.MITIGATION,
        as_of_date=as_of_date,
        agent_id=_AGENT_ID,
        context_hash="deadbeef",
    )
    db_session.commit()


def test_sweep_recovers_a_stranded_pending_row_with_no_job_item(database: Database, db_session):
    purchase_order_id = uuid4()
    _make_pending_summary(db_session, purchase_order_id, _TODAY)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 1
    assert len(dispatcher.dispatched) == 1

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)
    assert len(items) == 1
    assert items[0]["item_type"] == "MITIGATION_SUMMARY_REGEN"
    assert items[0]["status"] == "PENDING"
    assert items[0]["id"] in dispatcher.dispatched


def test_sweep_leaves_a_pending_row_alone_when_a_job_item_already_covers_it(database: Database, db_session):
    purchase_order_id = uuid4()
    _make_pending_summary(db_session, purchase_order_id, _TODAY)

    job_queue = JobQueueRepository(db_session)
    job_context = PenaltyJobItemContextRepository(db_session)
    run = job_queue.create_run(job_type="MITIGATION_SUMMARY_REGEN", trigger_type="ON_DEMAND")
    item = job_queue.enqueue(
        run["id"],
        item_type="MITIGATION_SUMMARY_REGEN",
        dedupe_key=f"{purchase_order_id}:{_TODAY.isoformat()}:MITIGATION_SUMMARY_REGEN",
        max_attempts=5,
    )
    job_context.create(
        job_item_id=item["id"],
        purchase_order_id=purchase_order_id,
        projection_date=_TODAY,
        task_type="MITIGATION_SUMMARY_REGEN",
    )
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_a_row_outside_the_day_window_alone(database: Database, db_session):
    stale_date = _TODAY - timedelta(days=10)
    _make_pending_summary(db_session, uuid4(), stale_date)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_ready_and_failed_rows_alone(database: Database, db_session):
    repo = PenaltySummaryRepository(db_session)
    ready_po_id = uuid4()
    repo.create_pending(
        purchase_order_id=ready_po_id,
        summary_type=SummaryType.MITIGATION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        context_hash="hash1",
    )
    repo.mark_ready(
        purchase_order_id=ready_po_id,
        summary_type=SummaryType.MITIGATION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        model_name="gpt-test",
        summary="Accepting the penalty is best.",
    )
    failed_po_id = uuid4()
    repo.create_pending(
        purchase_order_id=failed_po_id,
        summary_type=SummaryType.MITIGATION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        context_hash="hash2",
    )
    repo.mark_failed(
        purchase_order_id=failed_po_id,
        summary_type=SummaryType.MITIGATION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        error_message="boom",
    )
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_is_idempotent_on_a_second_run(database: Database, db_session):
    _make_pending_summary(db_session, uuid4(), _TODAY)

    dispatcher = _RecordingDispatcher()
    settings = Settings(summary={"pending_sweep_days": 3})

    first = sweep_stranded_pending_mitigation_summaries(dispatcher, database, settings, today=_TODAY)
    second = sweep_stranded_pending_mitigation_summaries(dispatcher, database, settings, today=_TODAY)

    assert first.recovered_count == 1
    assert second.recovered_count == 0
    assert len(dispatcher.dispatched) == 1


def test_sweep_finds_nothing_when_there_are_no_pending_rows(database: Database):
    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert result.job_run_id is None
    assert dispatcher.dispatched == []


def test_sweep_does_not_recover_stranded_projection_summary_rows(database: Database, db_session):
    """The two sweeps are independent: a stranded penalty_summary
    (PROJECTION) row must not be picked up by the mitigation-summary sweep."""
    PenaltySummaryRepository(db_session).create_pending(
        purchase_order_id=uuid4(),
        summary_type=SummaryType.PROJECTION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        context_hash="deadbeef",
    )
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_mitigation_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []
