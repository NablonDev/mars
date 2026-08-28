"""API endpoint chaining all four fine-projection/fine-mitigation steps for
one order in a single call: projection -> projection summary -> mitigation
options -> mitigation summary.

Domain-neutral location (not nested under fine_projection/ or fine_mitigation/,
and not orders.py -- that file is order CRUD only per its own docstring):
this orchestrates both domains' services in one handler."""

from collections.abc import Callable
from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    enqueue_and_dispatch_mitigation_summary_job,
    enqueue_and_dispatch_summary_job,
    get_fine_mitigation_service,
    get_fine_mitigation_summary_job_runner,
    get_fine_mitigation_summary_service,
    get_fine_projection_service,
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
from app.schemas.fine_mitigation.mitigations import (
    MitigationOptionResponse,
    MitigationOptionsResponse,
)
from app.schemas.fine_mitigation.summaries import (
    MitigationSummaryResponse,
    MitigationSummaryStatusResponse,
)
from app.schemas.fine_projection.projections import ProjectionResultResponse
from app.schemas.fine_projection.summaries import (
    ProjectionSummaryResponse,
    ProjectionSummaryStatusResponse,
)
from app.schemas.fine_runs import OrderFineRunRequest, OrderFineRunResponse
from app.services.fine_mitigation.service import FineMitigationService
from app.services.fine_mitigation.summary import FineMitigationSummaryService
from app.services.fine_projection.service import FineProjectionService
from app.services.fine_projection.summary import FineProjectionSummaryService

router = APIRouter(tags=["fine-runs"])


@router.post(
    "/orders/{order_id}/fine-runs",
    response_model=OrderFineRunResponse,
    responses={202: {"model": OrderFineRunResponse}},
)
def run_full_chain(
    order_id: str,
    body: OrderFineRunRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    projection_service: FineProjectionService = Depends(get_fine_projection_service),
    fine_projection_summary_service: FineProjectionSummaryService = Depends(
        get_fine_projection_summary_service
    ),
    mitigation_service: FineMitigationService = Depends(get_fine_mitigation_service),
    fine_mitigation_summary_service: FineMitigationSummaryService = Depends(
        get_fine_mitigation_summary_service
    ),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
    job_dispatcher: JobDispatcher = Depends(get_job_dispatcher),
    run_projection_summary_job: Callable[[str, date, str, UUID | None], None] = Depends(
        get_fine_projection_summary_job_runner
    ),
    run_mitigation_summary_job: Callable[[str, date, str, UUID | None], None] = Depends(
        get_fine_mitigation_summary_job_runner
    ),
    settings: Settings = Depends(get_settings),
) -> OrderFineRunResponse:
    # Step 1: projection, synchronous and inline -- same posture as
    # POST /orders/{order_id}/projections/runs and POST /orders/{order_id}/mitigation-options/runs.
    projection_result = projection_service.run_for_order(
        order_id,
        body.projection_date,
        body.stacking_mode_override,
    )

    # Step 2: projection summary, for the exact day step 1 just projected.
    projection_summary_pending = False
    projection_summary_job = fine_projection_summary_service.get_or_schedule(
        order_id,
        as_of_date=projection_result.projection_date,
        force_regenerate=body.force_regenerate_projection_summary,
    )

    if projection_summary_job.status == SummaryStatus.READY:
        assert projection_summary_job.output is not None
        projection_summary_response = ProjectionSummaryStatusResponse(
            order_id=projection_summary_job.order_id,
            as_of_date=projection_summary_job.as_of_date,
            prompt_version=projection_summary_job.prompt_version,
            status=SummaryStatus.READY,
            summary=ProjectionSummaryResponse.model_validate(projection_summary_job.output),
        )
    else:
        job_item_id = enqueue_and_dispatch_summary_job(
            session,
            job_queue_repository,
            job_dispatcher,
            order_id,
            projection_summary_job.as_of_date,
            settings,
        )
        background_tasks.add_task(
            run_projection_summary_job,
            order_id,
            projection_summary_job.as_of_date,
            projection_summary_job.prompt_version,
            job_item_id,
        )
        projection_summary_pending = True
        projection_summary_response = ProjectionSummaryStatusResponse(
            order_id=projection_summary_job.order_id,
            as_of_date=projection_summary_job.as_of_date,
            prompt_version=projection_summary_job.prompt_version,
            status=SummaryStatus.PENDING,
        )

    # Step 3: mitigation options, ranked against the projection step 1 just
    # computed -- projection_result.projection_date, not body.projection_date,
    # so NO_PROJECTION_EXISTS can never happen here (unlike the standalone
    # POST /orders/{order_id}/mitigation-options/runs, which requires the caller to
    # have already run a projection for that date separately).
    mitigation_projection_date, options = mitigation_service.run_for_order(
        order_id, projection_result.projection_date
    )
    mitigation_options_response = MitigationOptionsResponse(
        order_id=order_id,
        projection_date=mitigation_projection_date,
        options=[MitigationOptionResponse.model_validate(o) for o in options],
    )

    # Step 4: mitigation summary, for the same day.
    mitigation_summary_pending = False
    mitigation_summary_job = fine_mitigation_summary_service.get_or_schedule(
        order_id,
        as_of_date=mitigation_projection_date,
        force_regenerate=body.force_regenerate_mitigation_summary,
    )

    if mitigation_summary_job.status == SummaryStatus.READY:
        assert mitigation_summary_job.output is not None
        mitigation_summary_response = MitigationSummaryStatusResponse(
            order_id=mitigation_summary_job.order_id,
            as_of_date=mitigation_summary_job.as_of_date,
            prompt_version=mitigation_summary_job.prompt_version,
            status=SummaryStatus.READY,
            summary=MitigationSummaryResponse.model_validate(mitigation_summary_job.output),
        )
    else:
        job_item_id = enqueue_and_dispatch_mitigation_summary_job(
            session,
            job_queue_repository,
            job_dispatcher,
            order_id,
            mitigation_summary_job.as_of_date,
            settings,
        )
        background_tasks.add_task(
            run_mitigation_summary_job,
            order_id,
            mitigation_summary_job.as_of_date,
            mitigation_summary_job.prompt_version,
            job_item_id,
        )
        mitigation_summary_pending = True
        mitigation_summary_response = MitigationSummaryStatusResponse(
            order_id=mitigation_summary_job.order_id,
            as_of_date=mitigation_summary_job.as_of_date,
            prompt_version=mitigation_summary_job.prompt_version,
            status=SummaryStatus.PENDING,
        )

    if projection_summary_pending or mitigation_summary_pending:
        response.status_code = 202

    return OrderFineRunResponse(
        projection=ProjectionResultResponse.model_validate(projection_result),
        projection_summary=projection_summary_response,
        mitigation_options=mitigation_options_response,
        mitigation_summary=mitigation_summary_response,
    )
