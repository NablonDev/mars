"""Tests for app/workers/penalty_projection.py -- the recovery sweep that
closes the penalty-projection-summary two-commit durability gap between
`SummaryServiceBase.get_or_schedule`'s PENDING ledger write and the
caller's separate `job_item` write (see that module's docstring), plus
`enqueue_daily_run` (moved here from `app/workers/loop.py`, which stayed
domain-agnostic).

Was `tests/unit/workers/test_worker_fine_projection.py` (`fine`/`fines` ->
`penalty`/`penalties` rename, against `app.repositories.job_queue`/
`app.repositories.fine_projection.summary`'s pre-restructure order-keyed
shape) -- rewritten against the Phase 2/3 `process`/`penalties`
repositories, keyed by the UUID surrogate `purchase_order_id` and the
merged `penalty_summary` table's `summary_type` discriminator.

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
from app.models.enums import SummaryType
from app.repositories.common.master_data import MasterDataRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.summary import PenaltySummaryRepository
from app.repositories.process.job_queue import JobQueueRepository
from app.workers.penalty_projection import enqueue_daily_run, sweep_stranded_pending_projection_summaries

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
        summary_type=SummaryType.PROJECTION,
        as_of_date=as_of_date,
        agent_id=_AGENT_ID,
        context_hash="deadbeef",
    )
    db_session.commit()


def test_sweep_recovers_a_stranded_pending_row_with_no_job_item(database: Database, db_session):
    purchase_order_id = uuid4()
    _make_pending_summary(db_session, purchase_order_id, _TODAY)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 1
    assert len(dispatcher.dispatched) == 1

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)
    assert len(items) == 1
    assert items[0]["item_type"] == "PROJECTION_SUMMARY_REGEN"
    assert items[0]["status"] == "PENDING"
    assert items[0]["id"] in dispatcher.dispatched


def test_sweep_leaves_a_pending_row_alone_when_a_job_item_already_covers_it(database: Database, db_session):
    purchase_order_id = uuid4()
    _make_pending_summary(db_session, purchase_order_id, _TODAY)

    from app.repositories.penalties.job_context import PenaltyJobItemContextRepository

    job_queue = JobQueueRepository(db_session)
    job_context = PenaltyJobItemContextRepository(db_session)
    run = job_queue.create_run(job_type="PROJECTION_SUMMARY_REGEN", trigger_type="ON_DEMAND")
    item = job_queue.enqueue(
        run["id"],
        item_type="PROJECTION_SUMMARY_REGEN",
        dedupe_key=f"{purchase_order_id}:{_TODAY.isoformat()}:PROJECTION_SUMMARY_REGEN",
        max_attempts=5,
    )
    job_context.create(
        job_item_id=item["id"],
        purchase_order_id=purchase_order_id,
        projection_date=_TODAY,
        task_type="PROJECTION_SUMMARY_REGEN",
    )
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_a_pending_row_alone_even_when_its_job_item_is_terminal(database: Database, db_session):
    """A DEAD or SUCCEEDED job_item still counts as "the queue already
    knows about this row" -- the sweep's job is to catch rows the queue
    never learned about at all, not to re-drive one whose job_item the
    queue already resolved one way or another."""
    purchase_order_id = uuid4()
    _make_pending_summary(db_session, purchase_order_id, _TODAY)

    from app.repositories.penalties.job_context import PenaltyJobItemContextRepository

    job_queue = JobQueueRepository(db_session)
    job_context = PenaltyJobItemContextRepository(db_session)
    run = job_queue.create_run(job_type="PROJECTION_SUMMARY_REGEN", trigger_type="ON_DEMAND")
    item = job_queue.enqueue(
        run["id"],
        item_type="PROJECTION_SUMMARY_REGEN",
        dedupe_key=f"{purchase_order_id}:{_TODAY.isoformat()}:PROJECTION_SUMMARY_REGEN",
        max_attempts=5,
    )
    job_context.create(
        job_item_id=item["id"],
        purchase_order_id=purchase_order_id,
        projection_date=_TODAY,
        task_type="PROJECTION_SUMMARY_REGEN",
    )
    job_queue.claim_batch("worker-1", limit=1, job_item_ids=[item["id"]])
    job_queue.mark_dead(item["id"], "worker-1", "boom", "SOME_FAILURE")
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_a_row_outside_the_day_window_alone(database: Database, db_session):
    stale_date = _TODAY - timedelta(days=10)
    _make_pending_summary(db_session, uuid4(), stale_date)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_leaves_ready_and_failed_rows_alone(database: Database, db_session):
    repo = PenaltySummaryRepository(db_session)
    ready_po_id = uuid4()
    repo.create_pending(
        purchase_order_id=ready_po_id,
        summary_type=SummaryType.PROJECTION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        context_hash="hash1",
    )
    repo.mark_ready(
        purchase_order_id=ready_po_id,
        summary_type=SummaryType.PROJECTION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        model_name="gpt-test",
        summary="All clear.",
    )
    failed_po_id = uuid4()
    repo.create_pending(
        purchase_order_id=failed_po_id,
        summary_type=SummaryType.PROJECTION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        context_hash="hash2",
    )
    repo.mark_failed(
        purchase_order_id=failed_po_id,
        summary_type=SummaryType.PROJECTION,
        as_of_date=_TODAY,
        agent_id=_AGENT_ID,
        error_message="boom",
    )
    db_session.commit()

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert dispatcher.dispatched == []


def test_sweep_is_idempotent_on_a_second_run(database: Database, db_session):
    _make_pending_summary(db_session, uuid4(), _TODAY)

    dispatcher = _RecordingDispatcher()
    settings = Settings(summary={"pending_sweep_days": 3})

    first = sweep_stranded_pending_projection_summaries(dispatcher, database, settings, today=_TODAY)
    second = sweep_stranded_pending_projection_summaries(dispatcher, database, settings, today=_TODAY)

    assert first.recovered_count == 1
    assert second.recovered_count == 0
    assert len(dispatcher.dispatched) == 1


def test_sweep_recovers_multiple_stranded_rows_in_one_pass(database: Database, db_session):
    _make_pending_summary(db_session, uuid4(), _TODAY)
    _make_pending_summary(db_session, uuid4(), _TODAY)

    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 2
    assert len(dispatcher.dispatched) == 2


def test_sweep_finds_nothing_when_there_are_no_pending_rows(database: Database):
    dispatcher = _RecordingDispatcher()
    result = sweep_stranded_pending_projection_summaries(
        dispatcher, database, Settings(summary={"pending_sweep_days": 3}), today=_TODAY
    )

    assert result.recovered_count == 0
    assert result.job_run_id is None
    assert dispatcher.dispatched == []


# ---------------------------------------------------------------------
# enqueue_daily_run
# ---------------------------------------------------------------------


def _seed_open_and_closed_purchase_orders(
    database: Database, open_count: int, closed_count: int
) -> tuple[list[UUID], list[UUID]]:
    with database.session() as session:
        master_data = MasterDataRepository(session)
        purchase_orders = PurchaseOrderRepository(session)
        retailer = master_data.add_retailer("RET-ENQ", "Retailer Enqueue", None, "SUM")
        material = master_data.add_material("MAT-ENQ", None)
        plant = master_data.add_plant("PLANT-ENQ", None, None)

        open_ids: list[UUID] = []
        closed_ids: list[UUID] = []
        for i in range(open_count + closed_count):
            po = purchase_orders.create_purchase_order(
                purchase_order_number=f"PO-ENQ-{i}",
                retailer_id=retailer["id"],
                order_date=date(2026, 8, 1),
                requested_delivery_date=date(2026, 8, 10),
                required_ship_date=date(2026, 8, 8),
            )
            purchase_orders.add_line(
                purchase_order_id=po["id"],
                line_number="10",
                ordered_quantity=10,
                unit_price=1.0,
                material_id=material["id"],
                plant_id=plant["id"],
            )
            if i < open_count:
                open_ids.append(po["id"])
            else:
                closed_ids.append(po["id"])
                purchase_orders.set_order_status(po["id"], "DELIVERED")
    return open_ids, closed_ids


def test_enqueue_daily_run_enqueues_one_order_run_per_open_purchase_order_and_dispatches_each(database):
    open_ids, _closed_ids = _seed_open_and_closed_purchase_orders(database, open_count=2, closed_count=1)
    dispatcher = _RecordingDispatcher()
    settings = Settings(summary={"business_timezone": "UTC"})

    result = enqueue_daily_run(dispatcher, database, settings, projection_date=date(2026, 8, 13))

    assert result.purchase_order_count == 2
    assert result.enqueued_count == 2
    assert len(dispatcher.dispatched) == 2

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)

    assert all(i["item_type"] == "ORDER_RUN" for i in items)
    assert {i["dedupe_key"].split(":")[0] for i in items} == {str(po_id) for po_id in open_ids}


def test_enqueue_daily_run_defaults_to_today_in_business_timezone(database):
    _seed_open_and_closed_purchase_orders(database, open_count=1, closed_count=0)
    dispatcher = _RecordingDispatcher()
    settings = Settings(summary={"business_timezone": "UTC"})

    from datetime import UTC, datetime

    result = enqueue_daily_run(dispatcher, database, settings)

    with database.session() as session:
        items = JobQueueRepository(session).list_run_items(result.job_run_id)

    assert items[0]["dedupe_key"].split(":")[1] == datetime.now(UTC).date().isoformat()
