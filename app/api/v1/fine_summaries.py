"""API endpoints for generating and polling fine summaries."""

from collections.abc import Callable
from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    enqueue_and_dispatch_summary_job,
    get_fine_summary_job_runner,
    get_fine_summary_service,
    get_job_dispatcher,
    get_job_queue_repository,
    get_session,
)
from app.core.config import Settings, get_settings
from app.models.enums import SummaryStatus
from app.queue.interfaces import JobDispatcher
from app.repositories.job_queue import JobQueueRepository
from app.schemas.fine_summaries import (
    FineSummaryRequest,
    FineSummaryResponse,
    FineSummaryStatusResponse,
)
from app.services.fine_summary import FineSummaryService

router = APIRouter(tags=["fine-summaries"])


@router.post(
    "/orders/{order_id}/summary",
    response_model=FineSummaryResponse | FineSummaryStatusResponse,
    responses={202: {"model": FineSummaryStatusResponse}},
)
def generate_fine_summary(
    order_id: str,
    body: FineSummaryRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    fine_summary_service: FineSummaryService = Depends(get_fine_summary_service),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
    job_dispatcher: JobDispatcher = Depends(get_job_dispatcher),
    run_summary_job: Callable[[str, date, str, UUID | None], None] = Depends(get_fine_summary_job_runner),
    settings: Settings = Depends(get_settings),
) -> FineSummaryResponse | FineSummaryStatusResponse:
    job = fine_summary_service.get_or_schedule(
        order_id,
        as_of_date=body.as_of_date,
        force_regenerate=body.force_regenerate,
    )

    if job.status == SummaryStatus.READY:
        assert job.output is not None
        return FineSummaryResponse.model_validate(job.output)

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

    return FineSummaryStatusResponse(
        order_id=job.order_id,
        as_of_date=job.as_of_date,
        prompt_version=job.prompt_version,
        status=SummaryStatus.PENDING,
    )


@router.get(
    "/orders/{order_id}/summary",
    response_model=FineSummaryStatusResponse,
)
def get_fine_summary_status(
    order_id: str,
    as_of_date: date | None = None,
    fine_summary_service: FineSummaryService = Depends(get_fine_summary_service),
) -> FineSummaryStatusResponse:
    job = fine_summary_service.get_status(
        order_id,
        as_of_date=as_of_date,
    )

    return FineSummaryStatusResponse(
        order_id=job.order_id,
        as_of_date=job.as_of_date,
        prompt_version=job.prompt_version,
        status=job.status,
        summary=(
            FineSummaryResponse.model_validate(job.output)
            if job.output is not None
            else None
        ),
        error_message=job.error_message,
    )
