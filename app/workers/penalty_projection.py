"""Penalty-projection job execution, recovery sweep, and daily enqueue.

Was `app/workers/fine_projection.py` (`fine`/`fines` -> `penalty`/`penalties`
rename, per the approved plan's naming convention -- mirrors the
`app/services/penalties/projection/` rename). Rewritten against the Phase
2/3 `common`/`process`/`penalties` repositories and services; stale
imports (`app.repositories.job_queue`, `app.repositories.order`,
`app.repositories.fine_rule`, `app.repositories.fine_master_data`,
`app.repositories.fine_projection.*`, `app.repositories.agent_registry`,
`app.services.fine_projection.*`) are gone.

Everything here is penalty-projection-specific: the ORDER_RUN/
PROJECTION_SUMMARY_REGEN per-item work (`run_projection`, `run_summary`),
the nightly enqueue of one ORDER_RUN per OPEN purchase order
(`enqueue_daily_run`), the recovery sweep for stranded PENDING
`penalty_summary` (PROJECTION) rows
(`sweep_stranded_pending_projection_summaries`), and the PO
delivery-change-request expiry sweep
(`sweep_expired_po_delivery_change_requests`).

The domain-shaped business key (`purchase_order_id`, `projection_date`,
`stacking_mode_override`, `force_regenerate_summary`) a `ClaimedJob` used to
carry directly is gone from `process.job_item`/`ClaimedJob` (see
`app.repositories.process.job_queue`'s and `app.queue.types`'s module
docstrings) -- every per-item function below re-reads it from the matching
`penalties.penalty_job_item_context` row via `PenaltyJobItemContextRepository`,
keyed on `job.job_item_id`.
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
from app.models.enums import JobRunType, JobTaskType, SummaryStatus, SummaryType
from app.queue.interfaces import JobDispatcher
from app.queue.types import ClaimedJob, SweepResult
from app.repositories.common.fulfillment import FulfillmentRepository
from app.repositories.common.master_data import MasterDataRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository, describe_no_open_orders
from app.repositories.penalties.delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.penalties.job_context import (
    PenaltyJobItemContextRepository,
    PenaltyJobRunContextRepository,
)
from app.repositories.penalties.projection import ActualPenaltyRepository, PenaltyProjectionRepository
from app.repositories.penalties.rule import PenaltyRuleRepository
from app.repositories.penalties.summary import PenaltySummaryRepository
from app.repositories.process.agent_registry import AgentRegistryRepository
from app.repositories.process.job_queue import JobQueueRepository
from app.services.penalties.delivery_change import PoDeliveryChangeRequestService
from app.services.penalties.projection.service import ProjectionService
from app.services.penalties.projection.summary_service import ProjectionSummaryService

logger = logging.getLogger(__name__)


def _missing_context_error(job_item_id: UUID) -> ValueError:
    """Non-retryable: `execute_job`/`classify_failure` treat a bare
    `ValueError` (not an `AppError`) as DEAD_LETTER -- a job item with no
    matching context row can never succeed on retry."""
    return ValueError(
        f"No penalty_job_item_context found for job_item_id={job_item_id!r} -- "
        "cannot execute this job without its purchase_order_id/projection_date."
    )


def _build_projection_service(session) -> ProjectionService:
    return ProjectionService(
        purchase_orders=PurchaseOrderRepository(session),
        fulfillment=FulfillmentRepository(session),
        rules=PenaltyRuleRepository(session),
        master_data=MasterDataRepository(session),
        projections=PenaltyProjectionRepository(session),
    )


def run_projection(job: ClaimedJob, database: Database) -> None:
    with database.session() as session:
        context = PenaltyJobItemContextRepository(session).get(job.job_item_id)
        if context is None:
            raise _missing_context_error(job.job_item_id)

        service = _build_projection_service(session)
        service.run_for_purchase_order(
            context["purchase_order_id"],
            context["projection_date"],
            context["stacking_mode_override"],
        )


def run_summary(
    job: ClaimedJob,
    database: Database,
    llm: AzureOpenAIChatClient,
    *,
    heartbeat: Callable[[], None] | None,
) -> None:
    # Keep the LLM call outside the projection session's lifetime.
    with database.session() as session:
        context = PenaltyJobItemContextRepository(session).get(job.job_item_id)
        if context is None:
            raise _missing_context_error(job.job_item_id)

        service = ProjectionSummaryService(
            purchase_orders=PurchaseOrderRepository(session),
            summaries=PenaltySummaryRepository(session),
            agent_registry=AgentRegistryRepository(session),
            job_queue=JobQueueRepository(session),
            job_context=PenaltyJobItemContextRepository(session),
            llm=llm,
            rules=PenaltyRuleRepository(session),
            master_data=MasterDataRepository(session),
            projections=PenaltyProjectionRepository(session),
            actual_penalties=ActualPenaltyRepository(session),
            projection_service=_build_projection_service(session),
        )
        summary_job = service.get_or_schedule(
            context["purchase_order_id"],
            as_of_date=context["projection_date"],
            force_regenerate=context["force_regenerate_summary"],
        )
        if summary_job.status == SummaryStatus.PENDING:
            # run_generation persists failure and re-raises for worker
            # classification (SummaryServiceBase.run_generation's own
            # docstring). That persisted FAILED row is only a `flush()`,
            # not a `commit()` -- letting the exception propagate straight
            # out of this `with database.session()` block would hit
            # `Database.session()`'s own `except Exception:
            # session.rollback()` and silently undo it. Commit explicitly
            # before re-raising so the FAILED ledger row survives for the
            # worker loop's retry/dead-letter classification to act on;
            # verified live against Postgres (see this phase's report).
            try:
                service.run_generation(
                    context["purchase_order_id"],
                    summary_job.as_of_date,
                    heartbeat=heartbeat,
                )
            except Exception:
                session.commit()
                raise


@dataclass
class EnqueueResult:
    job_run_id: UUID
    purchase_order_count: int
    enqueued_count: int
    no_open_orders_note: str | None = None


def enqueue_daily_run(
    job_dispatcher: JobDispatcher,
    database: Database,
    settings: Settings,
    *,
    projection_date: date | None = None,
    stacking_mode_override: str | None = None,
) -> EnqueueResult:
    """Create the daily job_run and enqueue one ORDER_RUN job_item per OPEN
    purchase order, with its matching `penalty_job_item_context` row attached
    in the same transaction.

    Mirrors `app.api.v1.job_runs._trigger_penalty_projection_batch`'s
    enqueue logic (kept independent -- that route is HTTP-request-scoped,
    this is the nightly/standalone entry point `scripts/ops/run_daily_batch.py`
    calls with no server running).
    """
    tz = ZoneInfo(settings.summary.business_timezone)
    resolved_date = projection_date or datetime.now(tz).date()

    with database.session() as session:
        job_queue = JobQueueRepository(session)
        purchase_orders = PurchaseOrderRepository(session)
        job_run_context = PenaltyJobRunContextRepository(session)
        job_item_context = PenaltyJobItemContextRepository(session)

        open_purchase_orders = purchase_orders.list_purchase_orders(order_status="OPEN")

        no_open_orders_note: str | None = None
        if not open_purchase_orders:
            no_open_orders_note = describe_no_open_orders(purchase_orders.count_by_status())
            if no_open_orders_note:
                logger.warning(no_open_orders_note)

        run = job_queue.create_run(
            job_type=JobTaskType.ORDER_RUN,
            trigger_type=JobRunType.SCHEDULED_DAILY,
            requested_item_count=len(open_purchase_orders),
        )
        job_run_id = run["id"]
        job_run_context.create(
            job_run_id=job_run_id,
            projection_date=resolved_date,
            stacking_mode_override=stacking_mode_override,
        )

        item_ids: list[UUID] = []
        for purchase_order in open_purchase_orders:
            dedupe_key = f"{purchase_order['id']}:{resolved_date.isoformat()}:{JobTaskType.ORDER_RUN}"
            item = job_queue.enqueue(
                job_run_id,
                item_type=JobTaskType.ORDER_RUN,
                dedupe_key=dedupe_key,
                max_attempts=settings.job_queue.max_attempts,
            )
            # A collision may return an item belonging to an earlier run, or a
            # context row already attached to it -- only attach/dispatch items
            # created for this run (mirrors the API route's own guard).
            if item is None or item["job_run_id"] != job_run_id:
                continue
            if job_item_context.get(item["id"]) is None:
                job_item_context.create(
                    job_item_id=item["id"],
                    purchase_order_id=purchase_order["id"],
                    projection_date=resolved_date,
                    task_type=JobTaskType.ORDER_RUN,
                    stacking_mode_override=stacking_mode_override,
                )
            item_ids.append(item["id"])

        if len(item_ids) != len(open_purchase_orders):
            logger.info(
                "Enqueued %d of %d OPEN purchase order(s); %d already in flight",
                len(item_ids),
                len(open_purchase_orders),
                len(open_purchase_orders) - len(item_ids),
            )
            job_queue.set_requested_item_count(job_run_id, len(item_ids))

    # Commit before dispatch so workers can safely claim the items.
    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    return EnqueueResult(
        job_run_id=job_run_id,
        purchase_order_count=len(open_purchase_orders),
        enqueued_count=len(item_ids),
        no_open_orders_note=no_open_orders_note,
    )


def sweep_stranded_pending_projection_summaries(
    job_dispatcher: JobDispatcher,
    database: Database,
    settings: Settings,
    *,
    today: date | None = None,
) -> SweepResult:
    """Recover PENDING `penalty_summary` (PROJECTION) rows with no
    corresponding job item.

    Dispatches only after the DB transaction commits.
    """
    tz = ZoneInfo(settings.summary.business_timezone)
    resolved_today = today or datetime.now(tz).date()
    earliest_as_of_date = resolved_today - timedelta(days=settings.summary.pending_sweep_days)

    with database.session() as session:
        summaries = PenaltySummaryRepository(session)
        stranded = summaries.find_stranded_pending(
            earliest_as_of_date,
            resolved_today,
            SummaryType.PROJECTION,
        )

        if not stranded:
            return SweepResult(recovered_count=0)

        job_queue = JobQueueRepository(session)
        job_item_context = PenaltyJobItemContextRepository(session)

        run = job_queue.create_run(
            job_type=JobTaskType.PROJECTION_SUMMARY_REGEN,
            trigger_type=JobRunType.MANUAL_BATCH,
            requested_item_count=len(stranded),
        )
        job_run_id = run["id"]

        item_ids: list[UUID] = []
        for row in stranded:
            dedupe_key = (
                f"{row['purchase_order_id']}:{row['as_of_date'].isoformat()}:"
                f"{JobTaskType.PROJECTION_SUMMARY_REGEN}"
            )
            item = job_queue.enqueue(
                job_run_id,
                item_type=JobTaskType.PROJECTION_SUMMARY_REGEN,
                dedupe_key=dedupe_key,
                max_attempts=settings.job_queue.max_attempts,
            )
            if item is None or item["job_run_id"] != job_run_id:
                continue
            if job_item_context.get(item["id"]) is None:
                job_item_context.create(
                    job_item_id=item["id"],
                    purchase_order_id=row["purchase_order_id"],
                    projection_date=row["as_of_date"],
                    task_type=JobTaskType.PROJECTION_SUMMARY_REGEN,
                )
            item_ids.append(item["id"])

        if len(item_ids) != len(stranded):
            job_queue.set_requested_item_count(job_run_id, len(item_ids))

    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    logger.info(
        "Recovery sweep: recovered %d stranded projection-summary row(s) under job_run_id=%s",
        len(item_ids),
        job_run_id,
    )

    return SweepResult(
        recovered_count=len(item_ids),
        job_run_id=job_run_id,
    )


def sweep_expired_po_delivery_change_requests(
    database: Database,
    *,
    as_of: datetime | None = None,
) -> SweepResult:
    """Recover PENDING PO delivery-change requests whose `expires_at` has
    passed -- transitions each to EXPIRED and re-triggers projection for its
    purchase order (see PoDeliveryChangeRequestService.expire_stale).
    Unlike sweep_stranded_pending_projection_summaries, no JobDispatcher is
    involved: the status flip and the run_for_purchase_order re-trigger both
    happen inline, before commit, since no LLM call is needed for either.
    """
    with database.session() as session:
        master_data = MasterDataRepository(session)
        service = PoDeliveryChangeRequestService(
            purchase_orders=PurchaseOrderRepository(session),
            delivery_change_requests=PoDeliveryChangeRequestRepository(session),
            projection_service=_build_projection_service(session),
            master_data=master_data,
        )
        expired = service.expire_stale(as_of)

    if expired:
        logger.info(
            "Recovery sweep: expired %d stale PO delivery-change request(s).",
            len(expired),
        )

    return SweepResult(recovered_count=len(expired))
