"""API endpoints for generating and polling fine summaries."""

from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Response

from app.api.dependencies import (
    get_fine_summary_job_runner,
    get_fine_summary_service,
)
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
    fine_summary_service: FineSummaryService = Depends(get_fine_summary_service),
    run_summary_job: Callable[[str, date, str], None] = Depends(get_fine_summary_job_runner),
) -> FineSummaryResponse | FineSummaryStatusResponse:
    job = fine_summary_service.get_or_schedule(
        order_id,
        as_of_date=body.as_of_date,
        force_regenerate=body.force_regenerate,
    )

    if job.status == "READY":
        assert job.output is not None
        return FineSummaryResponse.model_validate(job.output)

    background_tasks.add_task(
        run_summary_job,
        order_id,
        job.as_of_date,
        job.prompt_version,
    )

    response.status_code = 202

    return FineSummaryStatusResponse(
        order_id=job.order_id,
        as_of_date=job.as_of_date,
        prompt_version=job.prompt_version,
        status="PENDING",
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
        summary=(FineSummaryResponse.model_validate(job.output) if job.output is not None else None),
        error_message=job.error_message,
    )
