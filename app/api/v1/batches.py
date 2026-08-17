"""API endpoints for triggering and monitoring batch job runs and items.

Provides operational visibility into batch execution without requiring
direct database access, including run status, item-level outcomes, and
backend-specific dispatch semantics.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_job_dispatcher,
    get_job_queue_repository,
    get_order_repository,
    get_session,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.models import JobRun
from app.models.enums import JobItemStatus, JobRunType, JobTaskType
from app.queue.interfaces import JobDispatcher
from app.repositories.job_queue import JobQueueRepository
from app.repositories.order import OrderRepository, describe_no_open_orders
from app.schemas.batches import (
    BatchItemListResponse,
    BatchItemResponse,
    BatchRunRequest,
    BatchRunResponse,
    BatchStatusCounts,
    BatchStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batches", tags=["batches"])

# Backend-derived execution notes. Do not provide an ETA or timestamp:
# pickup timing depends on the configured queue backend.
_EXECUTION_NOTES: dict[str, str] = {
    "service_bus": "Dispatched to the queue; a consumer will pick it up when available.",
    "postgres": "Enqueued; will be processed by the next scheduled batch drain.",
}


def _execution_note(job_queue_backend: str) -> str:
    return _EXECUTION_NOTES.get(
        job_queue_backend,
        f"Enqueued under job_queue_backend={job_queue_backend!r}; "
        f"pickup timing depends on that backend.",
    )


@router.post("/run", response_model=BatchRunResponse, status_code=202)
def trigger_batch_run(
    body: BatchRunRequest,
    session: Session = Depends(get_session),
    orders: OrderRepository = Depends(get_order_repository),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
    job_dispatcher: JobDispatcher = Depends(get_job_dispatcher),
    settings: Settings = Depends(get_settings),
) -> BatchRunResponse:
    """Enqueue a full batch for all OPEN orders and return the created run.

    The endpoint only creates and dispatches jobs; projection and summary
    generation are performed asynchronously by workers. All job rows are
    committed before dispatch to ensure workers cannot observe uncommitted
    items.
    """
    projection_date = body.projection_date or datetime.now(UTC).date()
    open_orders = orders.list_orders(order_status="OPEN")

    no_open_orders_note: str | None = None
    if not open_orders:
        no_open_orders_note = describe_no_open_orders(orders.count_by_status())
        if no_open_orders_note:
            logger.warning(no_open_orders_note)

    run = job_queue_repository.create_run(
        run_type=JobRunType.MANUAL_BATCH,
        projection_date=projection_date,
        stacking_mode_override=body.stacking_mode_override,
        triggered_by="api",
        requested_item_count=len(open_orders),
    )

    item_ids: list[UUID] = []
    for order in open_orders:
        item = job_queue_repository.enqueue(
            job_run_id=run["id"],
            order_id=order["order_id"],
            projection_date=projection_date,
            task_type=JobTaskType.ORDER_RUN,
            stacking_mode_override=body.stacking_mode_override,
            max_attempts=settings.job_queue_max_attempts,
        )
        # A collision may return an item belonging to an earlier run.
        # Only include items created for this run so completion counts remain
        # consistent with the run being triggered.
        if item is not None and item["job_run_id"] == run["id"]:
            item_ids.append(item["id"])

    if len(item_ids) != len(open_orders):
        job_queue_repository.set_requested_item_count(run["id"], len(item_ids))

    session.commit()

    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    return BatchRunResponse(
        job_run_id=run["id"],
        requested_item_count=len(item_ids),
        dispatch_mode=settings.job_queue_backend,
        # The normal note promises a drain will process this; with zero items
        # that would be actively misleading.
        execution_note=no_open_orders_note or _execution_note(settings.job_queue_backend),
    )


@router.get("/{job_run_id}", response_model=BatchStatusResponse)
def get_batch_status(
    job_run_id: UUID,
    session: Session = Depends(get_session),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
) -> BatchStatusResponse:
    # A run with zero items is valid; absence of the JobRun distinguishes
    # "run does not exist" from "run exists but has no items".
    if session.get(JobRun, job_run_id) is None:
        raise NotFoundError(f"No batch run found with job_run_id={job_run_id}")

    summary = job_queue_repository.get_run_summary(job_run_id)
    counts = summary["counts"]

    # Completion is based on persisted items, not requested_item_count.
    # A run with no persisted items has not started and cannot be complete.
    total_items = summary["total_items"]
    is_complete = (
        total_items > 0 and (counts[JobItemStatus.SUCCEEDED] + counts[JobItemStatus.DEAD]) == total_items
    )

    return BatchStatusResponse(
        job_run_id=job_run_id,
        requested_item_count=summary["requested_item_count"],
        counts=BatchStatusCounts(**counts),
        total_items=summary["total_items"],
        is_complete=is_complete,
    )


@router.get("/{job_run_id}/items", response_model=BatchItemListResponse)
def list_batch_items(
    job_run_id: UUID,
    status: JobItemStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
) -> BatchItemListResponse:
    rows = job_queue_repository.list_run_items(
        job_run_id,
        status=status,
        limit=limit,
        offset=offset
    )

    items = [
        BatchItemResponse(
            id=row["id"],
            order_id=row["order_id"],
            projection_date=row["projection_date"],
            task_type=row["task_type"],
            status=row["status"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            last_error_code=row["last_error_code"],
            # Expose only a safe message derived from the error code.
            # Never return raw exception text; see BatchItemResponse.
            last_error_message=(
                f"Job failed with error code {row['last_error_code']}"
                if row["last_error_code"]
                else None
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )
        for row in rows
    ]

    return BatchItemListResponse(
        job_run_id=job_run_id,
        items=items,
        limit=limit,
        offset=offset
    )
