"""Tests for app/workers/fine_projection.py -- the recovery sweep that
closes the fine-projection-summary two-commit durability gap between
`FineProjectionSummaryService.get_or_schedule`'s PENDING ledger write and the
caller's separate `job_item` write (see that module's docstring), plus
`enqueue_daily_run` (moved here from `app/workers/loop.py`, which stayed
domain-agnostic).

Run against the same in-memory SQLite `database`/`db_session` fixtures as
the rest of the queue-layer tests (see conftest.py) -- `db_session` sets
up the "stranded" state directly via the repositories, and
`sweep_stranded_pending_projection_summaries`/`enqueue_daily_run` are exercised end
to end against the shared `database` fixture.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.db.session import Database
from app.repositories.fine_projection.summary import FineProjectionSummaryRepository
from app.repositories.job_queue import JobQueueRepository
from app.workers.fine_projection import enqueue_daily_run, sweep_stranded_pending_projection_summaries

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
    FineProjectionSummaryRepository(db_session).create_pending(
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
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 1
    assert len(dispatcher.dispatched) == 1

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)
    assert len(items) == 1
    assert items[0]["order_id"] == "ORD-STRANDED"
    assert items[0]["projection_date"] == _TODAY
    assert items[0]["task_type"] == "PROJECTION_SUMMARY_REGEN"
    assert items[0]["status"] == "PENDING"
    assert items[0]["id"] in dispatcher.dispatched


def test_sweep_leaves_a_pending_row_alone_when_a_job_item_already_covers_it(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-COVERED", _TODAY)

    repo = JobQueueRepository(db_session)
    run = repo.create_run(run_type="ON_DEMAND", projection_date=_TODAY)
    repo.enqueue(run["id"], "ORD-COVERED", _TODAY, "PROJECTION_SUMMARY_REGEN", max_attempts=5)
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
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
    item = repo.enqueue(run["id"], "ORD-DEAD-COVERED", _TODAY, "PROJECTION_SUMMARY_REGEN", max_attempts=5)
    repo.claim_batch("worker-1", limit=1, job_item_ids=[item["id"]])
    repo.mark_dead(item["id"], "worker-1", "boom", "SOME_FAILURE")
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_a_row_outside_the_day_window_alone(database: Database, db_session):
    stale_date = _TODAY - timedelta(days=10)
    _make_pending_summary(db_session, "ORD-TOO-OLD", stale_date)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_ready_and_failed_rows_alone(database: Database, db_session):
    repo = FineProjectionSummaryRepository(db_session)
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
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_is_idempotent_on_a_second_run(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-TWICE", _TODAY)

    dispatcher = _RecordingDispatcher()
    settings = Settings(summary_pending_sweep_days=3)

    first = sweep_stranded_pending_projection_summaries(dispatcher, database, settings, today=_TODAY)
    second = sweep_stranded_pending_projection_summaries(dispatcher, database, settings, today=_TODAY)

    assert first.recovered_count == 1
    assert second.recovered_count == 0
    assert len(dispatcher.dispatched) == 1


def test_sweep_recovers_multiple_stranded_rows_in_one_pass(database: Database, db_session):
    _make_pending_summary(db_session, "ORD-MULTI-1", _TODAY)
    _make_pending_summary(db_session, "ORD-MULTI-2", _TODAY)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 2
    assert len(dispatcher.dispatched) == 2


def test_sweep_finds_nothing_when_there_are_no_pending_rows(database: Database):
    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary_pending_sweep_days=3), today=_TODAY
    )

    assert result.recovered_count == 0
    assert result.job_run_id is None
    assert dispatcher.dispatched == []


# ---------------------------------------------------------------------
# enqueue_daily_run
# ---------------------------------------------------------------------


def _seed_open_and_closed_orders(database, open_ids: list[str], closed_ids: list[str]) -> None:
    from app.repositories.fine_master_data import MasterDataRepository
    from app.repositories.order import OrderRepository

    with database.session() as session:
        master_data = MasterDataRepository(session)
        orders = OrderRepository(session)
        master_data.add_retailer("RET-ENQ", "Retailer Enqueue", None, "SUM")
        master_data.add_sku("SKU-ENQ", "MAT-ENQ", None)
        master_data.add_location("LOC-ENQ", None, None)

        for order_id in open_ids + closed_ids:
            orders.create_order(
                order_id=order_id,
                retailer_id="RET-ENQ",
                sku_id="SKU-ENQ",
                ship_from_location_id="LOC-ENQ",
                order_qty=10,
                unit_price=1.0,
                order_date=date(2026, 8, 1),
                requested_delivery_date=date(2026, 8, 10),
                required_ship_date=date(2026, 8, 8),
            )
        for order_id in closed_ids:
            orders.set_order_status(order_id, "DELIVERED")


def test_enqueue_daily_run_enqueues_one_order_run_per_open_order_and_dispatches_each(database):
    _seed_open_and_closed_orders(database, open_ids=["ORD-OPEN-1", "ORD-OPEN-2"], closed_ids=["ORD-CLOSED"])
    dispatcher = _RecordingDispatcher()
    settings = Settings(penalty_business_timezone="UTC")

    result = enqueue_daily_run(dispatcher, database, settings, projection_date=date(2026, 8, 13))

    assert result.order_count == 2
    assert result.enqueued_count == 2
    assert len(dispatcher.dispatched) == 2

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)

    assert {i["order_id"] for i in items} == {"ORD-OPEN-1", "ORD-OPEN-2"}
    assert all(i["task_type"] == "ORDER_RUN" for i in items)
    assert all(i["projection_date"] == date(2026, 8, 13) for i in items)


def test_enqueue_daily_run_defaults_to_today_in_business_timezone(database):
    _seed_open_and_closed_orders(database, open_ids=["ORD-TODAY"], closed_ids=[])
    dispatcher = _RecordingDispatcher()
    settings = Settings(penalty_business_timezone="UTC")

    from datetime import UTC, datetime

    result = enqueue_daily_run(dispatcher, database, settings)

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)

    assert items[0]["projection_date"] == datetime.now(UTC).date()
