"""API endpoints for computing and retrieving ranked mitigation options,
and for chaining mitigation options -> mitigation summary in one call."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    enqueue_and_dispatch_mitigation_summary_job,
    get_fine_mitigation_service,
    get_fine_mitigation_summary_job_runner,
    get_fine_mitigation_summary_service,
    get_job_dispatcher,
    get_job_queue_repository,
    get_order_repository,
    get_session,
)
from app.core.config import Settings, get_settings
from app.models.enums import SummaryStatus
from app.queue.interfaces import JobDispatcher
from app.repositories.job_queue import JobQueueRepository
from app.repositories.order import OrderRepository
from app.schemas.fine_mitigation.mitigations import (
    MitigationOptionResponse,
    MitigationOptionsResponse,
    MitigationRunRequest,
    MitigationRunResponse,
    OrderMitigationRequest,
)
from app.schemas.fine_mitigation.summaries import (
    MitigationSummaryResponse,
    MitigationSummaryStatusResponse,
)
from app.services.fine_mitigation.service import FineMitigationService
from app.services.fine_mitigation.summary import FineMitigationSummaryService

router = APIRouter(tags=["mitigation-options"])


@router.post(
    "/orders/{order_id}/mitigation-options",
    response_model=MitigationOptionsResponse,
    status_code=201,
)
def create_order_mitigation_options(
    order_id: str,
    body: OrderMitigationRequest,
    mitigation_service: FineMitigationService = Depends(get_fine_mitigation_service),
) -> MitigationOptionsResponse:
    projection_date, options = mitigation_service.run_for_order(order_id, body.projection_date)
    return MitigationOptionsResponse(
        order_id=order_id,
        projection_date=projection_date,
        options=[MitigationOptionResponse.model_validate(o) for o in options],
    )


@router.get(
    "/orders/{order_id}/mitigation-options",
    response_model=MitigationOptionsResponse,
)
def get_order_mitigation_options(
    order_id: str,
    orders: OrderRepository = Depends(get_order_repository),
    mitigation_service: FineMitigationService = Depends(get_fine_mitigation_service),
) -> MitigationOptionsResponse:
    orders.require_order(order_id)
    projection_date, rows = mitigation_service.get_latest(order_id)
    return MitigationOptionsResponse(
        order_id=order_id,
        projection_date=projection_date,
        options=[MitigationOptionResponse.model_validate(r) for r in rows],
    )


@router.post(
    "/orders/{order_id}/mitigation-run",
    response_model=MitigationRunResponse,
    responses={202: {"model": MitigationRunResponse}},
)
def run_mitigation_and_summary(
    order_id: str,
    body: MitigationRunRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    mitigation_service: FineMitigationService = Depends(get_fine_mitigation_service),
    fine_mitigation_summary_service: FineMitigationSummaryService = Depends(
        get_fine_mitigation_summary_service
    ),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
    job_dispatcher: JobDispatcher = Depends(get_job_dispatcher),
    run_summary_job: Callable[[str, date, str, UUID | None], None] = Depends(
        get_fine_mitigation_summary_job_runner
    ),
    settings: Settings = Depends(get_settings),
) -> MitigationRunResponse:
    projection_date, options = mitigation_service.run_for_order(order_id, body.projection_date)
    mitigation_options_response = MitigationOptionsResponse(
        order_id=order_id,
        projection_date=projection_date,
        options=[MitigationOptionResponse.model_validate(o) for o in options],
    )

    summary_job = fine_mitigation_summary_service.get_or_schedule(
        order_id,
        as_of_date=projection_date,
        force_regenerate=body.force_regenerate_summary,
    )

    if summary_job.status == SummaryStatus.READY:
        assert summary_job.output is not None
        summary_response = MitigationSummaryStatusResponse(
            order_id=summary_job.order_id,
            as_of_date=summary_job.as_of_date,
            prompt_version=summary_job.prompt_version,
            status=SummaryStatus.READY,
            summary=MitigationSummaryResponse.model_validate(summary_job.output),
        )
    else:
        job_item_id = enqueue_and_dispatch_mitigation_summary_job(
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
        summary_response = MitigationSummaryStatusResponse(
            order_id=summary_job.order_id,
            as_of_date=summary_job.as_of_date,
            prompt_version=summary_job.prompt_version,
            status=SummaryStatus.PENDING,
        )

    return MitigationRunResponse(
        mitigation_options=mitigation_options_response,
        summary=summary_response,
    )
