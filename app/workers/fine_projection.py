"""Fine-projection job execution, recovery sweep, and daily enqueue.

Everything in this module is fine_projection-specific: the ORDER_RUN/
PROJECTION_SUMMARY_REGEN per-item work (`run_projection`, `run_summary`), the
nightly enqueue of one ORDER_RUN per OPEN order (`enqueue_daily_run`,
moved out of the domain-agnostic `loop.py`), and the recovery sweep for
stranded PENDING `projection_summary` rows (`sweep_stranded_pending_projection_summaries`,
moved out of the now-removed `sweep.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import Settings
from app.db.session import Database
from app.models.enums import JobRunType, JobTaskType, SummaryStatus
from app.queue.interfaces import JobDispatcher
from app.queue.types import ClaimedJob, SweepResult
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_projection.po_delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.fine_projection.projection import ProjectionRepository
from app.repositories.fine_projection.summary import FineProjectionSummaryRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.job_queue import JobQueueRepository
from app.repositories.order import OrderRepository, describe_no_open_orders
from app.services.fine_projection.po_delivery_change import PoDeliveryChangeRequestService
from app.services.fine_projection.service import FineProjectionService
from app.services.fine_projection.summary import FineProjectionSummaryService

logger = logging.getLogger(__name__)

_SWEEP_TRIGGERED_BY = "recovery-sweep"


def run_projection(job: ClaimedJob, database: Database) -> None:
    with database.session() as session:
        service = FineProjectionService(
            orders=OrderRepository(session),
            rules=FineRuleRepository(session),
            master_data=MasterDataRepository(session),
            projections=ProjectionRepository(session),
        )
        service.run_for_order(job.order_id, job.projection_date, job.stacking_mode_override)


def run_summary(
    job: ClaimedJob,
    database: Database,
    llm: AzureOpenAIChatClient,
    *,
    heartbeat: Callable[[], None] | None,
) -> None:
    # Keep the LLM call outside the projection session's lifetime.
    with database.session() as session:
        service = FineProjectionSummaryService(
            orders=OrderRepository(session),
            rules=FineRuleRepository(session),
            master_data=MasterDataRepository(session),
            projections=ProjectionRepository(session),
            summaries=FineProjectionSummaryRepository(session),
            prompt_registry=PromptRegistryRepository(session),
            llm=llm,
        )
        fine_job = service.get_or_schedule(
            job.order_id,
            as_of_date=job.projection_date,
            force_regenerate=job.force_regenerate_summary,
        )
        if fine_job.status == SummaryStatus.PENDING:
            # run_generation persists failure and re-raises for worker classification.
            service.run_generation(
                fine_job.order_id,
                fine_job.as_of_date,
                fine_job.prompt_version,
                heartbeat=heartbeat,
            )


@dataclass
class EnqueueResult:
    job_run_id: UUID
    order_count: int
    enqueued_count: int
    no_open_orders_note: str | None = None


def enqueue_daily_run(
    job_dispatcher: JobDispatcher,
    database: Database,
    settings: Settings,
    *,
    projection_date: date | None = None,
    stacking_mode_override: str | None = None,
    triggered_by: str = "nightly-batch",
) -> EnqueueResult:
    """Create the daily run and enqueue one ORDER_RUN per OPEN order."""
    tz = ZoneInfo(settings.penalty_business_timezone)
    resolved_date = projection_date or datetime.now(tz).date()

    with database.session() as session:
        repo = JobQueueRepository(session)
        orders = OrderRepository(session)
        order_ids = [o["order_id"] for o in orders.list_orders(order_status="OPEN")]

        no_open_orders_note: str | None = None
        if not order_ids:
            no_open_orders_note = describe_no_open_orders(orders.count_by_status())
            if no_open_orders_note:
                logger.warning(no_open_orders_note)

        run = repo.create_run(
            run_type=JobRunType.SCHEDULED_DAILY,
            projection_date=resolved_date,
            stacking_mode_override=stacking_mode_override,
            triggered_by=triggered_by,
            requested_item_count=len(order_ids),
        )
        job_run_id = run["id"]

        enqueued_count = repo.enqueue_many(
            job_run_id,
            [
                {
                    "order_id": order_id,
                    "projection_date": resolved_date,
                    "task_type": JobTaskType.ORDER_RUN,
                    "stacking_mode_override": stacking_mode_override,
                }
                for order_id in order_ids
            ],
            max_attempts=settings.job_queue_max_attempts,
        )

        # enqueue_many skips in-flight orders, so reconcile the run count.
        if enqueued_count != len(order_ids):
            logger.info(
                "Enqueued %d of %d OPEN orders; %d already in flight",
                enqueued_count,
                len(order_ids),
                len(order_ids) - enqueued_count,
            )
            repo.set_requested_item_count(
                job_run_id,
                enqueued_count,
            )

        item_ids = [
            item["id"]
            for item in repo.list_run_items(
                job_run_id,
                limit=max(len(order_ids), 1),
            )
        ]

    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    return EnqueueResult(
        job_run_id=job_run_id,
        order_count=len(order_ids),
        enqueued_count=enqueued_count,
        no_open_orders_note=no_open_orders_note,
    )


def sweep_stranded_pending_projection_summaries(
    job_dispatcher: JobDispatcher,
    database: Database,
    settings: Settings,
    *,
    today: date | None = None,
) -> SweepResult:
    """Recover PENDING summary rows with no corresponding job item.

    Dispatches only after the DB transaction commits.
    """
    tz = ZoneInfo(settings.penalty_business_timezone)
    resolved_today = today or datetime.now(tz).date()
    earliest_as_of_date = resolved_today - timedelta(days=settings.summary_pending_sweep_days)

    with database.session() as session:
        repo = JobQueueRepository(session)
        stranded = repo.find_stranded_pending_projection_summaries(
            earliest_as_of_date,
            resolved_today,
        )

        if not stranded:
            return SweepResult(recovered_count=0)

        run = repo.create_run(
            # Use MANUAL_BATCH; triggered_by identifies the recovery sweep.
            run_type=JobRunType.MANUAL_BATCH,
            projection_date=resolved_today,
            triggered_by=_SWEEP_TRIGGERED_BY,
            requested_item_count=len(stranded),
        )
        job_run_id = run["id"]

        enqueued_count = repo.enqueue_many(
            job_run_id,
            [
                {
                    "order_id": row["order_id"],
                    "projection_date": row["as_of_date"],
                    "task_type": JobTaskType.PROJECTION_SUMMARY_REGEN,
                }
                for row in stranded
            ],
            max_attempts=settings.job_queue_max_attempts,
        )

        # Keep the run count aligned with items actually created.
        if enqueued_count != len(stranded):
            repo.set_requested_item_count(job_run_id, enqueued_count)

        item_ids = [
            item["id"]
            for item in repo.list_run_items(
                job_run_id,
                limit=max(enqueued_count, 1),
            )
        ]

    # Commit before dispatch so workers can safely claim the items.
    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    logger.info(
        "Recovery sweep: recovered %d stranded summary row(s) under job_run_id=%s",
        enqueued_count,
        job_run_id,
    )

    return SweepResult(
        recovered_count=enqueued_count,
        job_run_id=job_run_id,
    )


def sweep_expired_po_delivery_change_requests(
    database: Database,
    *,
    as_of: datetime | None = None,
) -> SweepResult:
    """Recover PENDING PO delivery-change requests whose `expires_at` has
    passed -- transitions each to EXPIRED and re-triggers projection for its
    order (see PoDeliveryChangeRequestService.expire_stale). Unlike
    sweep_stranded_pending_projection_summaries, no JobDispatcher is involved:
    the status flip and the run_for_order re-trigger both happen inline, before
    commit, since no LLM call is needed for either.
    """
    with database.session() as session:
        master_data = MasterDataRepository(session)
        service = PoDeliveryChangeRequestService(
            orders=OrderRepository(session),
            po_delivery_change_requests=PoDeliveryChangeRequestRepository(session),
            projection_service=FineProjectionService(
                orders=OrderRepository(session),
                rules=FineRuleRepository(session),
                master_data=master_data,
                projections=ProjectionRepository(session),
            ),
            master_data=master_data,
        )
        expired = service.expire_stale(as_of)

    if expired:
        logger.info(
            "Recovery sweep: expired %d stale PO delivery-change request(s).",
            len(expired),
        )

    return SweepResult(recovered_count=len(expired))
