"""API endpoints for generating and polling fine mitigation summaries.

Full mirror of app/api/v1/fine_projection/summaries.py's contract: 202-
then-poll, cache-hit short-circuit, force_regenerate support.
"""

from collections.abc import Callable
from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    enqueue_and_dispatch_mitigation_summary_job,
    get_fine_mitigation_summary_job_runner,
    get_fine_mitigation_summary_service,
    get_job_dispatcher,
    get_job_queue_repository,
    get_session,
)
from app.core.config import Settings, get_settings
from app.models.enums import SummaryStatus
from app.queue.interfaces import JobDispatcher
from app.repositories.job_queue import JobQueueRepository
from app.schemas.fine_mitigation.summaries import (
    MitigationSummaryRequest,
    MitigationSummaryResponse,
    MitigationSummaryStatusResponse,
)
from app.services.fine_mitigation.summary import FineMitigationSummaryService

router = APIRouter(tags=["fine-mitigation-summaries"])


@router.post(
    "/orders/{order_id}/mitigation-summary",
    response_model=MitigationSummaryResponse | MitigationSummaryStatusResponse,
    responses={202: {"model": MitigationSummaryStatusResponse}},
)
def generate_fine_mitigation_summary(
    order_id: str,
    body: MitigationSummaryRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    fine_mitigation_summary_service: FineMitigationSummaryService = Depends(
        get_fine_mitigation_summary_service
    ),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
    job_dispatcher: JobDispatcher = Depends(get_job_dispatcher),
    run_summary_job: Callable[[str, date, str, UUID | None], None] = Depends(
        get_fine_mitigation_summary_job_runner
    ),
    settings: Settings = Depends(get_settings),
) -> MitigationSummaryResponse | MitigationSummaryStatusResponse:
    job = fine_mitigation_summary_service.get_or_schedule(
        order_id,
        as_of_date=body.as_of_date,
        force_regenerate=body.force_regenerate,
    )

    if job.status == SummaryStatus.READY:
        assert job.output is not None
        return MitigationSummaryResponse.model_validate(job.output)

    # Cache miss: leave a durable job_item alongside the PENDING ledger
    # row `get_or_schedule` already wrote, so a crash mid-flight is
    # recoverable by the nightly reclaim sweep instead of stranded PENDING
    # forever.
    job_item_id = enqueue_and_dispatch_mitigation_summary_job(
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

    return MitigationSummaryStatusResponse(
        order_id=job.order_id,
        as_of_date=job.as_of_date,
        prompt_version=job.prompt_version,
        status=SummaryStatus.PENDING,
    )


@router.get(
    "/orders/{order_id}/mitigation-summary",
    response_model=MitigationSummaryStatusResponse,
)
def get_fine_mitigation_summary_status(
    order_id: str,
    as_of_date: date | None = None,
    fine_mitigation_summary_service: FineMitigationSummaryService = Depends(
        get_fine_mitigation_summary_service
    ),
) -> MitigationSummaryStatusResponse:
    job = fine_mitigation_summary_service.get_status(
        order_id,
        as_of_date=as_of_date,
    )

    return MitigationSummaryStatusResponse(
        order_id=job.order_id,
        as_of_date=job.as_of_date,
        prompt_version=job.prompt_version,
        status=job.status,
        summary=(MitigationSummaryResponse.model_validate(job.output) if job.output is not None else None),
        error_message=job.error_message,
    )
