"""API endpoints for running projections and retrieving projection history and exposure."""

from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Response

from app.api.dependencies import (
    get_fine_summary_job_runner,
    get_fine_summary_service,
    get_order_repository,
    get_projection_repository,
    get_projection_service,
)
from app.core.exceptions import NoProjectionExistsError, ValidationError
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.schemas.fine_summaries import FineSummaryResponse, FineSummaryStatusResponse
from app.schemas.projections import (
    ExposureResponse,
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
    if not body.order_id and not body.all_open:
        raise ValidationError("Pass either order_id or all_open=true")

    if body.all_open:
        results = projection_service.run_for_all_open(
            body.projection_date,
            body.stacking_mode_override,
        )
    else:
        results = [
            projection_service.run_for_order(
                body.order_id,
                body.projection_date,
                body.stacking_mode_override,
            )
        ]

    return [ProjectionResultResponse.model_validate(r) for r in results]


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
    projection_service: ProjectionService = Depends(get_projection_service),
    fine_summary_service: FineSummaryService = Depends(get_fine_summary_service),
    run_summary_job: Callable[[str, date, str], None] = Depends(get_fine_summary_job_runner),
) -> OrderRunResponse:
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

    if summary_job.status == "READY":
        assert summary_job.output is not None
        summary_response = FineSummaryStatusResponse(
            order_id=summary_job.order_id,
            as_of_date=summary_job.as_of_date,
            prompt_version=summary_job.prompt_version,
            status="READY",
            summary=FineSummaryResponse.model_validate(summary_job.output),
        )
    else:
        background_tasks.add_task(
            run_summary_job,
            order_id,
            summary_job.as_of_date,
            summary_job.prompt_version,
        )
        response.status_code = 202
        summary_response = FineSummaryStatusResponse(
            order_id=summary_job.order_id,
            as_of_date=summary_job.as_of_date,
            prompt_version=summary_job.prompt_version,
            status="PENDING",
        )

    return OrderRunResponse(
        projection=ProjectionResultResponse.model_validate(projection_result),
        summary=summary_response,
    )
