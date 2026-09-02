"""Penalty-mitigation job execution and recovery sweep.

Was `app/workers/fine_mitigation.py` (`fine`/`fines` -> `penalty`/`penalties`
rename, per the approved plan's naming convention). Rewritten against the
Phase 2/3 `common`/`process`/`penalties` repositories and services.

Everything here is penalty-mitigation-specific: the MITIGATION_RUN per-item
work (`run_mitigation` -- computes and persists fresh mitigation options via
`MitigationService.run_for_purchase_order`, the same compute path
`POST /penalties/mitigations` uses synchronously), the
MITIGATION_SUMMARY_REGEN per-item work (`run_mitigation_summary` --
regenerates the LLM summary over options that already exist; deliberately
NOT the same operation as `run_mitigation`, see that function's docstring),
and the recovery sweep for stranded PENDING `penalty_summary` (MITIGATION)
rows (`sweep_stranded_pending_mitigation_summaries`). Mirrors
`app/workers/penalty_projection.py`'s shape for the sibling domain --
mitigation itself has no nightly-enqueue equivalent (it's on-demand only,
via `PENALTY_MITIGATION_BATCH`/`app.api.v1.job_runs._trigger_penalty_
mitigation_batch`), so this module has no `enqueue_daily_run`-equivalent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.job_context import PenaltyJobItemContextRepository
from app.repositories.penalties.mitigation import MitigationInputRepository, MitigationOptionRepository
from app.repositories.penalties.projection import ActualPenaltyRepository, PenaltyProjectionRepository
from app.repositories.penalties.rule import PenaltyRuleRepository
from app.repositories.penalties.summary import PenaltySummaryRepository
from app.repositories.process.agent_registry import AgentRegistryRepository
from app.repositories.process.job_queue import JobQueueRepository
from app.services.penalties.mitigation.service import MitigationService
from app.services.penalties.mitigation.summary_service import MitigationSummaryService
from app.services.penalties.projection.service import ProjectionService

logger = logging.getLogger(__name__)


def _missing_context_error(job_item_id: UUID) -> ValueError:
    """Non-retryable: `execute_job`/`classify_failure` treat a bare
    `ValueError` (not an `AppError`) as DEAD_LETTER -- a job item with no
    matching context row can never succeed on retry. Mirrors
    `app.workers.penalty_projection`'s identical helper (kept as its own
    copy rather than a cross-module private import)."""
    return ValueError(
        f"No penalty_job_item_context found for job_item_id={job_item_id!r} -- "
        "cannot execute this job without its purchase_order_id/projection_date."
    )


def _build_projection_service(session) -> ProjectionService:
    """Mirrors `app.workers.penalty_projection`'s identical helper --
    `MitigationSummaryService`/`ProjectionSummaryService._assemble_mandatory_context`
    (via `_build_daily_history`) reads `projection_service.build_snapshot`,
    so a full `ProjectionService` is needed here too, not just the
    mitigation-specific repositories."""
    return ProjectionService(
        purchase_orders=PurchaseOrderRepository(session),
        fulfillment=FulfillmentRepository(session),
        rules=PenaltyRuleRepository(session),
        master_data=MasterDataRepository(session),
        projections=PenaltyProjectionRepository(session),
    )


def run_mitigation(job: ClaimedJob, database: Database) -> None:
    """MITIGATION_RUN: compute and persist fresh mitigation options for one
    purchase order against its already-persisted projection.

    This is the batch-dispatched twin of the synchronous
    `POST /penalties/mitigations` route
    (`app/api/v1/penalties/mitigations.py::run_penalty_mitigations`) --
    both ultimately call `MitigationService.run_for_purchase_order`, the
    single compute-and-persist entry point for this domain; behavior there
    is unchanged by this addition. Deliberately NOT
    `run_mitigation_summary` below: that function only regenerates the LLM
    summary over options that already exist and requires them to be
    present first (`NO_MITIGATION_OPTIONS_EXIST` otherwise) -- this
    function is what actually computes and persists those options in the
    first place, no LLM call involved.
    """
    with database.session() as session:
        context = PenaltyJobItemContextRepository(session).get(job.job_item_id)
        if context is None:
            raise _missing_context_error(job.job_item_id)

        service = MitigationService(
            purchase_orders=PurchaseOrderRepository(session),
            rules=PenaltyRuleRepository(session),
            master_data=MasterDataRepository(session),
            projections=PenaltyProjectionRepository(session),
            mitigation_inputs=MitigationInputRepository(session),
            mitigation_options=MitigationOptionRepository(session),
            projection_service=_build_projection_service(session),
        )
        service.run_for_purchase_order(context["purchase_order_id"], context["projection_date"])


def run_mitigation_summary(
    job: ClaimedJob,
    database: Database,
    llm: AzureOpenAIChatClient,
    *,
    heartbeat: Callable[[], None] | None,
) -> None:
    # Keep the LLM call outside the mitigation-options session's lifetime.
    with database.session() as session:
        context = PenaltyJobItemContextRepository(session).get(job.job_item_id)
        if context is None:
            raise _missing_context_error(job.job_item_id)

        projection_service: ProjectionService = _build_projection_service(session)
        service = MitigationSummaryService(
            purchase_orders=PurchaseOrderRepository(session),
            summaries=PenaltySummaryRepository(session),
            agent_registry=AgentRegistryRepository(session),
            job_queue=JobQueueRepository(session),
            job_context=PenaltyJobItemContextRepository(session),
            llm=llm,
            master_data=MasterDataRepository(session),
            mitigation_options=MitigationOptionRepository(session),
            actual_penalties=ActualPenaltyRepository(session),
            projection_service=projection_service,
        )
        summary_job = service.get_or_schedule(
            context["purchase_order_id"],
            as_of_date=context["projection_date"],
            force_regenerate=context["force_regenerate_summary"],
        )
        if summary_job.status == SummaryStatus.PENDING:
            # See app.workers.penalty_projection.run_summary's identical
            # try/except -- run_generation's FAILED-row persistence is only
            # a `flush()`; letting the exception propagate straight out of
            # this `with database.session()` block would hit
            # `Database.session()`'s own `except Exception:
            # session.rollback()` and silently undo it. Commit explicitly
            # before re-raising so the FAILED ledger row survives for the
            # worker loop's retry/dead-letter classification to act on.
            try:
                service.run_generation(
                    context["purchase_order_id"],
                    summary_job.as_of_date,
                    heartbeat=heartbeat,
                )
            except Exception:
                session.commit()
                raise


def sweep_stranded_pending_mitigation_summaries(
    job_dispatcher: JobDispatcher,
    database: Database,
    settings: Settings,
    *,
    today: date | None = None,
) -> SweepResult:
    """Recover PENDING `penalty_summary` (MITIGATION) rows with no
    corresponding job item -- full mirror of
    sweep_stranded_pending_projection_summaries for the mitigation-summary
    feature."""
    tz = ZoneInfo(settings.summary.business_timezone)
    resolved_today = today or datetime.now(tz).date()
    earliest_as_of_date = resolved_today - timedelta(days=settings.summary.pending_sweep_days)

    with database.session() as session:
        summaries = PenaltySummaryRepository(session)
        stranded = summaries.find_stranded_pending(
            earliest_as_of_date,
            resolved_today,
            SummaryType.MITIGATION,
        )

        if not stranded:
            return SweepResult(recovered_count=0)

        job_queue = JobQueueRepository(session)
        job_item_context = PenaltyJobItemContextRepository(session)

        run = job_queue.create_run(
            job_type=JobTaskType.MITIGATION_SUMMARY_REGEN,
            trigger_type=JobRunType.MANUAL_BATCH,
            requested_item_count=len(stranded),
        )
        job_run_id = run["id"]

        item_ids: list[UUID] = []
        for row in stranded:
            dedupe_key = (
                f"{row['purchase_order_id']}:{row['as_of_date'].isoformat()}:"
                f"{JobTaskType.MITIGATION_SUMMARY_REGEN}"
            )
            item = job_queue.enqueue(
                job_run_id,
                item_type=JobTaskType.MITIGATION_SUMMARY_REGEN,
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
                    task_type=JobTaskType.MITIGATION_SUMMARY_REGEN,
                )
            item_ids.append(item["id"])

        if len(item_ids) != len(stranded):
            job_queue.set_requested_item_count(job_run_id, len(item_ids))

    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    logger.info(
        "Recovery sweep: recovered %d stranded mitigation-summary row(s) under job_run_id=%s",
        len(item_ids),
        job_run_id,
    )

    return SweepResult(
        recovered_count=len(item_ids),
        job_run_id=job_run_id,
    )
