"""API endpoints for generating and polling fine projection summaries."""

from collections.abc import Callable
from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    enqueue_and_dispatch_summary_job,
    get_fine_projection_summary_job_runner,
    get_fine_projection_summary_service,
    get_job_dispatcher,
    get_job_queue_repository,
    get_session,
)
from app.core.config import Settings, get_settings
from app.models.enums import SummaryStatus
from app.queue.interfaces import JobDispatcher
from app.repositories.job_queue import JobQueueRepository
from app.schemas.fine_projection.summaries import (
    ProjectionSummaryRequest,
    ProjectionSummaryResponse,
    ProjectionSummaryStatusResponse,
)
from app.services.fine_projection.summary import FineProjectionSummaryService

router = APIRouter(tags=["fine-projection-summaries"])


@router.post(
    "/orders/{order_id}/projection-summary",
    response_model=ProjectionSummaryResponse | ProjectionSummaryStatusResponse,
    responses={202: {"model": ProjectionSummaryStatusResponse}},
)
def generate_fine_projection_summary(
    order_id: str,
    body: ProjectionSummaryRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    fine_projection_summary_service: FineProjectionSummaryService = Depends(
        get_fine_projection_summary_service
    ),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
    job_dispatcher: JobDispatcher = Depends(get_job_dispatcher),
    run_summary_job: Callable[[str, date, str, UUID | None], None] = Depends(
        get_fine_projection_summary_job_runner
    ),
    settings: Settings = Depends(get_settings),
) -> ProjectionSummaryResponse | ProjectionSummaryStatusResponse:
    job = fine_projection_summary_service.get_or_schedule(
        order_id,
        as_of_date=body.as_of_date,
        force_regenerate=body.force_regenerate,
    )

    if job.status == SummaryStatus.READY:
        assert job.output is not None
        return ProjectionSummaryResponse.model_validate(job.output)

    # Cache miss: leave a durable job_item alongside the PENDING ledger
    # row `get_or_schedule` already wrote, so a crash mid-flight is
    # recoverable by the nightly reclaim sweep instead of stranded PENDING
    # forever.
    job_item_id = enqueue_and_dispatch_summary_job(
        session,
        job_queue_repository,
        job_dispatcher,
        order_id,
        job.as_of_date,
        settings,
    )

    background_tasks.add_task(
        run_summary_job,
        order_id,
        job.as_of_date,
        job.prompt_version,
        job_item_id,
    )

    response.status_code = 202

    return ProjectionSummaryStatusResponse(
        order_id=job.order_id,
        as_of_date=job.as_of_date,
        prompt_version=job.prompt_version,
        status=SummaryStatus.PENDING,
    )


@router.get(
    "/orders/{order_id}/projection-summary",
    response_model=ProjectionSummaryStatusResponse,
)
def get_fine_projection_summary_status(
    order_id: str,
    as_of_date: date | None = None,
    fine_projection_summary_service: FineProjectionSummaryService = Depends(
        get_fine_projection_summary_service
    ),
) -> ProjectionSummaryStatusResponse:
    job = fine_projection_summary_service.get_status(
        order_id,
        as_of_date=as_of_date,
    )

    return ProjectionSummaryStatusResponse(
        order_id=job.order_id,
        as_of_date=job.as_of_date,
        prompt_version=job.prompt_version,
        status=job.status,
        summary=(ProjectionSummaryResponse.model_validate(job.output) if job.output is not None else None),
        error_message=job.error_message,
    )
