"""API endpoints for the generic `process.job_run`/`job_item` batch
trigger/status/items -- shared by `penalties` and, as of the CMIR follow-up
pass (Phase 7b), `cmir`, per the approved plan §5/§6.

Was `app/api/v1/batches.py` (`/batches/runs`, `/batches/{id}`,
`/batches/{id}/items`), moved to `/job-runs`, `/job-runs/{id}`,
`/job-runs/{id}/items`. Later relocated again from
`app/api/v1/penalties/batches.py` to this top-level module (architecture
review): these routes operate on the shared `process` schema, not the
`penalties` domain alone, and `_trigger_cmir_email_ingest` below imports
`CmirRunService` directly -- the same rationale that already placed
`workflow_threads`/`processing_errors` top-level rather than under a domain
folder. `POST /job-runs` now dispatches on
`body.job_type`: `PENALTY_PROJECTION_BATCH` (mapped internally to
`JobTaskType.ORDER_RUN`) runs the original penalty-projection batch logic;
`CMIR_EMAIL_INGEST` (mapped to the already-existing `JobTaskType.EMAIL_INGEST`
-- see `app.schemas.penalties.batches.CmirEmailIngestJobRunRequest`'s
docstring) delegates to `CmirRunService.start_email_ingest`.
`GET /job-runs/{id}` and `.../items` were already domain-agnostic (they key
purely off `job_run_id`, a generic `process.job_run` UUID) and need no
change for either `job_type`.

No generic "list every job_run, optionally filtered by job_type" endpoint
exists here -- `JobQueueRepository` has no such query (only
`get_run_summary`/`list_run_items`, both scoped to one known `job_run_id`;
`repositories/` is read-only this phase) -- flagged as the same kind of
capability gap as `CmirRunService.list_runs(view="batches")`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_job_dispatcher,
    get_job_queue_repository,
    get_penalty_job_item_context_repository,
    get_penalty_job_run_context_repository,
    get_purchase_order_repository,
    get_service,
    get_session,
)
from app.core.config import Settings, get_settings
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import NotFoundError
from app.models import JobRun
from app.models.enums import JobItemStatus, JobRunType, JobTaskType
from app.queue.interfaces import JobDispatcher
from app.repositories.common.purchase_order import PurchaseOrderRepository, describe_no_open_orders
from app.repositories.penalties.job_context import (
    PenaltyJobItemContextRepository,
    PenaltyJobRunContextRepository,
)
from app.repositories.process.job_queue import JobQueueRepository
from app.schemas.penalties.batches import (
    CmirEmailIngestJobRunRequest,
    JobItemListResponse,
    JobItemResponse,
    JobRunRequest,
    JobRunResponse,
    JobRunStatusCounts,
    JobRunStatusResponse,
    PenaltyProjectionBatchRequest,
)
from app.services.cmir.run_service import CmirRunService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["job-runs"])

# Backend-derived execution notes. Do not provide an ETA or timestamp:
# pickup timing depends on the configured queue backend.
_EXECUTION_NOTES: dict[str, str] = {
    "service_bus": "Dispatched to the queue; a consumer will pick it up when available.",
    "postgres": "Enqueued; will be processed by the next scheduled batch drain.",
}


def _execution_note(job_queue_backend: str) -> str:
    return _EXECUTION_NOTES.get(
        job_queue_backend,
        f"Enqueued under job_queue_backend={job_queue_backend!r}; pickup timing depends on that backend.",
    )


@router.post("/job-runs", response_model=Envelope[JobRunResponse], status_code=202)
def trigger_job_run(
    body: JobRunRequest,
    session: Session = Depends(get_session),
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    job_queue: JobQueueRepository = Depends(get_job_queue_repository),
    job_run_context: PenaltyJobRunContextRepository = Depends(get_penalty_job_run_context_repository),
    job_item_context: PenaltyJobItemContextRepository = Depends(get_penalty_job_item_context_repository),
    job_dispatcher: JobDispatcher = Depends(get_job_dispatcher),
    run_service: CmirRunService = Depends(get_service),
    settings: Settings = Depends(get_settings),
) -> Envelope[JobRunResponse]:
    """Dispatches on `body.job_type` (Pydantic discriminated union -- see
    `app.schemas.penalties.batches.JobRunRequest`)."""
    if isinstance(body, CmirEmailIngestJobRunRequest):
        return _trigger_cmir_email_ingest(body, run_service, job_queue, settings)
    return _trigger_penalty_projection_batch(
        body, session, purchase_orders, job_queue, job_run_context, job_item_context, job_dispatcher, settings
    )


def _trigger_cmir_email_ingest(
    body: CmirEmailIngestJobRunRequest,
    run_service: CmirRunService,
    job_queue: JobQueueRepository,
    settings: Settings,
) -> Envelope[JobRunResponse]:
    """`job_type=CMIR_EMAIL_INGEST` -- delegates to
    `CmirRunService.start_email_ingest`, which does its own
    `process.job_run`/`job_item` creation (and, unlike the penalty-projection
    branch, completes its work synchronously in-process -- fetching and
    persisting matching emails -- rather than only enqueueing for a worker;
    `status_code=202` is kept for a uniform response contract across both
    `job_type`s on this shared endpoint)."""
    result = run_service.start_email_ingest(
        max_workers=body.max_workers,
        subject_contains=body.subject_contains,
        unread_only=body.unread_only,
    )
    job_run_id = UUID(result["batch_id"])
    summary = job_queue.get_run_summary(job_run_id)

    if result["status"] == "no_new_emails":
        execution_note = "No new emails found matching the ingest filters."
    else:
        execution_note = (
            f"Email ingest completed; {summary['requested_item_count']} email(s) queued for processing."
        )

    return success_envelope(
        JobRunResponse(
            job_run_id=job_run_id,
            requested_item_count=summary["requested_item_count"],
            dispatch_mode=settings.job_queue.backend,
            execution_note=execution_note,
        ),
        message="CMIR email ingest job run completed.",
    )


def _trigger_penalty_projection_batch(
    body: PenaltyProjectionBatchRequest,
    session: Session,
    purchase_orders: PurchaseOrderRepository,
    job_queue: JobQueueRepository,
    job_run_context: PenaltyJobRunContextRepository,
    job_item_context: PenaltyJobItemContextRepository,
    job_dispatcher: JobDispatcher,
    settings: Settings,
) -> Envelope[JobRunResponse]:
    """Enqueue a full penalty-projection batch for every OPEN purchase order.

    Only creates and dispatches jobs; a worker actually running the
    projection for each item is out of scope this phase (see the phase
    report -- `app/workers/fine_projection.py` is stale, unrelated to this
    change, and not touched).
    """
    projection_date = body.projection_date or datetime.now(UTC).date()
    open_purchase_orders = purchase_orders.list_purchase_orders(order_status="OPEN")

    no_open_orders_note: str | None = None
    if not open_purchase_orders:
        no_open_orders_note = describe_no_open_orders(purchase_orders.count_by_status())
        if no_open_orders_note:
            logger.warning(no_open_orders_note)

    run = job_queue.create_run(
        job_type=JobTaskType.ORDER_RUN,
        trigger_type=JobRunType.MANUAL_BATCH,
        requested_item_count=len(open_purchase_orders),
    )
    job_run_context.create(
        job_run_id=run["id"],
        projection_date=projection_date,
        stacking_mode_override=body.stacking_mode_override,
    )

    item_ids: list[UUID] = []
    for purchase_order in open_purchase_orders:
        dedupe_key = f"{purchase_order['id']}:{projection_date.isoformat()}:{JobTaskType.ORDER_RUN}"
        item = job_queue.enqueue(
            run["id"],
            item_type=JobTaskType.ORDER_RUN,
            dedupe_key=dedupe_key,
            max_attempts=settings.job_queue.max_attempts,
        )
        # A collision may return an item belonging to an earlier run, or a
        # context row already attached to it -- only attach/dispatch items
        # created for this run.
        if item is None or item["job_run_id"] != run["id"]:
            continue
        if job_item_context.get(item["id"]) is None:
            job_item_context.create(
                job_item_id=item["id"],
                purchase_order_id=purchase_order["id"],
                projection_date=projection_date,
                task_type=JobTaskType.ORDER_RUN,
                stacking_mode_override=body.stacking_mode_override,
            )
        item_ids.append(item["id"])

    if len(item_ids) != len(open_purchase_orders):
        job_queue.set_requested_item_count(run["id"], len(item_ids))

    session.commit()

    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    return success_envelope(
        JobRunResponse(
            job_run_id=run["id"],
            requested_item_count=len(item_ids),
            dispatch_mode=settings.job_queue.backend,
            # The normal note promises a drain will process this; with zero
            # items that would be actively misleading.
            execution_note=no_open_orders_note or _execution_note(settings.job_queue.backend),
        ),
        message="Job run queued.",
    )


@router.get("/job-runs/{job_run_id}", response_model=Envelope[JobRunStatusResponse])
def get_job_run_status(
    job_run_id: UUID,
    session: Session = Depends(get_session),
    job_queue: JobQueueRepository = Depends(get_job_queue_repository),
) -> Envelope[JobRunStatusResponse]:
    # A run with zero items is valid; absence of the JobRun distinguishes
    # "run does not exist" from "run exists but has no items".
    if session.get(JobRun, job_run_id) is None:
        raise NotFoundError(
            code="JOB_RUN_NOT_FOUND", message=f"No job run found with job_run_id={job_run_id!r}"
        )

    summary = job_queue.get_run_summary(job_run_id)
    counts = summary["counts"]

    # Completion is based on persisted items, not requested_item_count. A
    # run with no persisted items has not started and cannot be complete.
    total_items = summary["total_items"]
    is_complete = (
        total_items > 0 and (counts[JobItemStatus.SUCCEEDED] + counts[JobItemStatus.DEAD]) == total_items
    )

    return success_envelope(
        JobRunStatusResponse(
            job_run_id=job_run_id,
            requested_item_count=summary["requested_item_count"],
            counts=JobRunStatusCounts(**counts),
            total_items=total_items,
            is_complete=is_complete,
        )
    )


@router.get("/job-runs/{job_run_id}/items", response_model=Envelope[JobItemListResponse])
def list_job_run_items(
    job_run_id: UUID,
    status: JobItemStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    job_queue: JobQueueRepository = Depends(get_job_queue_repository),
) -> Envelope[JobItemListResponse]:
    rows = job_queue.list_run_items(job_run_id, status=status, limit=limit, offset=offset)

    items = [
        JobItemResponse(
            id=row["id"],
            item_type=row["item_type"],
            dedupe_key=row["dedupe_key"],
            status=row["status"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            last_error_code=row["last_error_code"],
            # Expose only a safe message derived from the error code. Never
            # return raw exception text; see JobItemResponse.
            last_error_message=(
                f"Job failed with error code {row['last_error_code']}" if row["last_error_code"] else None
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )
        for row in rows
    ]

    return success_envelope(
        JobItemListResponse(job_run_id=job_run_id, items=items, limit=limit, offset=offset)
    )
