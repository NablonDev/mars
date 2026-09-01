"""API endpoints for `penalties.penalty_projection`: running/history/
exposure, the cross-PO open-projection list, and the projection-summary
trigger/poll contract.

Was `app/api/v1/fine_projection/{projections,summaries}.py`. The flagged
behavior change from the approved plan §5: today's single
`POST /projections/runs` (`all_open=true`) conflates "compute inline" with
"queue a batch job" -- split into `POST /api/v1/job-runs`
(`app/api/v1/job_runs.py`, async/queued) and this module's
`GET /penalty-projections?status=open` (synchronous list read).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import (
    get_penalty_projection_repository,
    get_projection_service,
    get_projection_summary_service,
    get_purchase_order_repository,
    parse_include,
)
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import AppError, NotFoundError
from app.models.enums import SummaryStatus
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.projection import PenaltyProjectionRepository
from app.schemas.penalties.projections import (
    PenaltyExposureResponse,
    PenaltyProjectionDetailResponse,
    PenaltyProjectionHistoryRow,
    PenaltyProjectionResultResponse,
    PenaltyProjectionRunRequest,
    PenaltyProjectionSummaryRequest,
    PenaltyProjectionSummaryResponse,
    PenaltyProjectionSummaryStatusResponse,
    ViolationResponse,
)
from app.services.penalties.projection.service import ProjectionService
from app.services.penalties.projection.summary_service import ProjectionSummaryService

router = APIRouter(tags=["penalty-projections"])

_INCLUDE_SUMMARY = parse_include(frozenset({"summary"}))


def _to_result_response(result) -> PenaltyProjectionResultResponse:
    return PenaltyProjectionResultResponse(
        purchase_order_id=UUID(result.order_id),
        projection_date=result.projection_date,
        days_to_delivery=result.days_to_delivery,
        shortage_probability=result.shortage_probability,
        delay_probability=result.delay_probability,
        violations=[
            ViolationResponse(
                violation_type=v.violation_type,
                rule_id=v.rule_id,
                probability=v.probability,
                penalty_amount=v.penalty_amount,
                expected_penalty_amount=v.expected_penalty_amount,
            )
            for v in result.violations
        ],
        total_expected_penalty_amount=result.total_expected_penalty_amount,
        stacking_mode=result.stacking_mode,
    )


def _try_get_summary_job(service: ProjectionSummaryService, purchase_order_id: UUID, as_of_date: date):
    """Pure read: resolve the cached summary job for one (PO, date), or
    `None` when no summary can be shown for it -- either none was ever
    requested (`NotFoundError`), or the date itself can never have had one
    (e.g. a forward-looking projection date still in the future relative to
    "today" -- `ValidationError(code="INVALID_AS_OF_DATE")`, a completely
    normal case for this domain's projections, not a caller error). Never
    schedules generation (approved plan §5's `?include=` semantics); any
    `AppError` here is treated as "nothing to include," not surfaced as the
    whole request's error.
    """
    try:
        return service.get_status(purchase_order_id, as_of_date=as_of_date)
    except AppError:
        return None


def _attach_summary(
    row: PenaltyProjectionDetailResponse,
    service: ProjectionSummaryService,
    purchase_order_id: UUID,
) -> None:
    job = _try_get_summary_job(service, purchase_order_id, row.projection_date)
    if job is None:
        return
    row.summary_status = SummaryStatus(job.status)
    if job.status == SummaryStatus.READY and job.output is not None:
        row.summary = PenaltyProjectionSummaryResponse.model_validate(job.output)


@router.post(
    "/purchase-orders/{purchase_order_id}/penalty-projections",
    response_model=Envelope[PenaltyProjectionResultResponse],
    status_code=201,
)
def run_penalty_projection(
    purchase_order_id: UUID,
    body: PenaltyProjectionRunRequest,
    projection_service: ProjectionService = Depends(get_projection_service),
) -> Envelope[PenaltyProjectionResultResponse]:
    result = projection_service.run_for_purchase_order(
        purchase_order_id, body.projection_date, body.stacking_mode_override
    )
    return success_envelope(_to_result_response(result), message="Penalty projection computed.")


@router.get(
    "/purchase-orders/{purchase_order_id}/penalty-projections",
    response_model=Envelope[list[PenaltyProjectionDetailResponse]],
)
def get_penalty_projection_history(
    purchase_order_id: UUID,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
    projection_summary_service: ProjectionSummaryService = Depends(get_projection_summary_service),
    include: set[str] = Depends(_INCLUDE_SUMMARY),
) -> Envelope[list[PenaltyProjectionDetailResponse]]:
    purchase_orders.require_purchase_order(purchase_order_id)
    rows = [
        PenaltyProjectionDetailResponse.model_validate(r) for r in projections.list_history(purchase_order_id)
    ]
    if "summary" in include:
        for row in rows:
            _attach_summary(row, projection_summary_service, purchase_order_id)
    return success_envelope(rows)


@router.get("/penalty-projections", response_model=Envelope[list[PenaltyProjectionHistoryRow]])
def list_open_penalty_projections(
    status: str = Query(default="OPEN"),
    projection_service: ProjectionService = Depends(get_projection_service),
) -> Envelope[list[PenaltyProjectionHistoryRow]]:
    """Flat, cross-PO projection list (approved plan §5, row 1b) -- not
    nested under a purchase order."""
    rows = projection_service.list_open_across_purchase_orders(status=status.upper())
    return success_envelope([PenaltyProjectionHistoryRow.model_validate(r) for r in rows])


@router.get(
    "/penalty-projections/{projection_id}",
    response_model=Envelope[PenaltyProjectionDetailResponse],
)
def get_penalty_projection(
    projection_id: UUID,
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
    projection_summary_service: ProjectionSummaryService = Depends(get_projection_summary_service),
    include: set[str] = Depends(_INCLUDE_SUMMARY),
) -> Envelope[PenaltyProjectionDetailResponse]:
    """Pure read, never schedules generation (approved plan §5)."""
    row = projections.get_by_id(projection_id)
    if row is None:
        raise NotFoundError(
            code="PROJECTION_NOT_FOUND",
            message=f"No penalty projection found with projection_id={projection_id!r}",
        )
    detail = PenaltyProjectionDetailResponse.model_validate(row)
    if "summary" in include:
        _attach_summary(detail, projection_summary_service, row["purchase_order_id"])
    return success_envelope(detail)


@router.get(
    "/purchase-orders/{purchase_order_id}/penalty-exposure",
    response_model=Envelope[PenaltyExposureResponse],
)
def get_penalty_exposure(
    purchase_order_id: UUID,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
) -> Envelope[PenaltyExposureResponse]:
    purchase_orders.require_purchase_order(purchase_order_id)
    latest = projections.get_latest(purchase_order_id)
    if latest is None:
        raise NotFoundError(
            code="NO_PROJECTION_EXISTS",
            message=f"No projections exist yet for purchase_order_id={purchase_order_id!r}",
        )
    return success_envelope(PenaltyExposureResponse.model_validate(latest))


@router.post(
    "/purchase-orders/{purchase_order_id}/penalty-projections/summary",
    response_model=Envelope[PenaltyProjectionSummaryStatusResponse],
    responses={202: {"model": Envelope[PenaltyProjectionSummaryStatusResponse]}},
)
def trigger_penalty_projection_summary(
    purchase_order_id: UUID,
    body: PenaltyProjectionSummaryRequest,
    response: Response,
    projection_summary_service: ProjectionSummaryService = Depends(get_projection_summary_service),
) -> Envelope[PenaltyProjectionSummaryStatusResponse]:
    job = projection_summary_service.get_or_schedule(
        purchase_order_id, as_of_date=body.as_of_date, force_regenerate=body.force_regenerate
    )

    if job.status == SummaryStatus.READY:
        assert job.output is not None
        return success_envelope(
            PenaltyProjectionSummaryStatusResponse(
                purchase_order_id=purchase_order_id,
                as_of_date=job.as_of_date,
                status=SummaryStatus.READY,
                summary=PenaltyProjectionSummaryResponse.model_validate(job.output),
            )
        )

    # PENDING: get_or_schedule already durably enqueued a process.job_run/
    # job_item (and committed) -- see ProjectionSummaryService._enqueue_
    # regeneration_job. No worker in this pass actually drains that queue
    # (out of scope, see the phase report); poll via
    # GET /penalty-projections/{projection_id}?include=summary once one does.
    response.status_code = 202
    return success_envelope(
        PenaltyProjectionSummaryStatusResponse(
            purchase_order_id=purchase_order_id,
            as_of_date=job.as_of_date,
            status=SummaryStatus.PENDING,
        ),
        message="Penalty projection summary generation queued.",
    )
