"""API endpoints for running projections and retrieving projection history and exposure."""

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
    get_order_repository,
    get_projection_repository,
    get_projection_service,
    get_session,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import NoProjectionExistsError, ValidationError
from app.models.enums import SummaryStatus
from app.queue.interfaces import JobDispatcher
from app.repositories.job_queue import JobQueueRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.schemas.fine_summaries import FineSummaryResponse, FineSummaryStatusResponse
from app.schemas.projections import (
    ExposureResponse,
    OrderProjectionRequest,
    OrderRunRequest,
    OrderRunResponse,
    ProjectionHistoryRow,
    ProjectionResultResponse,
    RunProjectionRequest,
)
from app.services.fine_summary import FineSummaryService
from app.services.projection import ProjectionService

router = APIRouter(tags=["projections"])


@router.post("/projections/run", response_model=list[ProjectionResultResponse])
def run_projection(
    body: RunProjectionRequest,
    projection_service: ProjectionService = Depends(get_projection_service),
) -> list:
    if not body.all_open:
        raise ValidationError(
            "This endpoint now only runs the all_open=true batch case. "
            "For a single order, use POST /orders/{order_id}/projections instead."
        )

    results = projection_service.run_for_all_open(
        body.projection_date,
        body.stacking_mode_override,
    )

    return [ProjectionResultResponse.model_validate(r) for r in results]


@router.post(
    "/orders/{order_id}/projections",
    response_model=ProjectionResultResponse,
    status_code=201,
)
def create_order_projection(
    order_id: str,
    body: OrderProjectionRequest,
    projection_service: ProjectionService = Depends(get_projection_service),
) -> ProjectionResultResponse:
    result = projection_service.run_for_order(
        order_id,
        body.projection_date,
        body.stacking_mode_override,
    )
    return ProjectionResultResponse.model_validate(result)


@router.get(
    "/orders/{order_id}/projections",
    response_model=list[ProjectionHistoryRow],
)
def get_projection_history(
    order_id: str,
    orders: OrderRepository = Depends(get_order_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
) -> list[dict]:
    orders.require_order(order_id)
    return projections.list_history(order_id)


@router.get("/orders/{order_id}/exposure", response_model=ExposureResponse)
def get_exposure(
    order_id: str,
    orders: OrderRepository = Depends(get_order_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
) -> dict:
    orders.require_order(order_id)

    latest = projections.get_latest(order_id)
    if latest is None:
        raise NoProjectionExistsError(f"No projections exist yet for order_id={order_id!r}")

    return latest


@router.post(
    "/orders/{order_id}/run",
    response_model=OrderRunResponse,
    responses={202: {"model": OrderRunResponse}},
)
def run_projection_and_summary(
    order_id: str,
    body: OrderRunRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    projection_service: ProjectionService = Depends(get_projection_service),
    fine_summary_service: FineSummaryService = Depends(get_fine_summary_service),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
    job_dispatcher: JobDispatcher = Depends(get_job_dispatcher),
    run_summary_job: Callable[[str, date, str, UUID | None], None] = Depends(get_fine_summary_job_runner),
    settings: Settings = Depends(get_settings),
) -> OrderRunResponse:
    # The projection half stays synchronous and inline -- queueing only
    # earns its keep at batch scale (see POST /batches/run for that path).
    projection_result = projection_service.run_for_order(
        order_id,
        body.projection_date,
        body.stacking_mode_override,
    )

    summary_job = fine_summary_service.get_or_schedule(
        order_id,
        as_of_date=projection_result.projection_date,
        force_regenerate=body.force_regenerate_summary,
    )

    if summary_job.status == SummaryStatus.READY:
        assert summary_job.output is not None
        summary_response = FineSummaryStatusResponse(
            order_id=summary_job.order_id,
            as_of_date=summary_job.as_of_date,
            prompt_version=summary_job.prompt_version,
            status=SummaryStatus.READY,
            summary=FineSummaryResponse.model_validate(summary_job.output),
        )
    else:
        # Cache miss: same durable job_item + dispatch + settle-on-
        # background-completion path as POST /orders/{order_id}/summary.
        job_item_id = enqueue_and_dispatch_summary_job(
            session,
            job_queue_repository,
            job_dispatcher,
            order_id,
            summary_job.as_of_date,
            settings,
        )
        background_tasks.add_task(
            run_summary_job,
            order_id,
            summary_job.as_of_date,
            summary_job.prompt_version,
            job_item_id,
        )
        response.status_code = 202
        summary_response = FineSummaryStatusResponse(
            order_id=summary_job.order_id,
            as_of_date=summary_job.as_of_date,
            prompt_version=summary_job.prompt_version,
            status=SummaryStatus.PENDING,
        )

    return OrderRunResponse(
        projection=ProjectionResultResponse.model_validate(projection_result),
        summary=summary_response,
    )
