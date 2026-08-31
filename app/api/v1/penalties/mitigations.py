"""API endpoints for `penalties.mitigation_option`: compute/list ranked
mitigation options for a projection, and the mitigation-summary
trigger/poll contract.

Was `app/api/v1/fine_mitigation/{mitigations,summaries}.py`. Mitigation
options are keyed by `(purchase_order_id, projection_date)`, not by their
own FK to `penalty_projection` (no such FK exists -- see
`app.models.penalties.mitigation.MitigationOption`'s docstring); `?projection_id=`
resolves to that `(purchase_order_id, projection_date)` pair via
`PenaltyProjectionRepository.get_by_id` first.

`POST /purchase-orders/{purchase_order_id}/penalty-mitigations/summary` is
one addition beyond this phase's literal route list (flagged in the phase
report): without a trigger endpoint symmetric to the projection-summary
one, `include=summary` below could never have anything to show.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import (
    get_mitigation_option_repository,
    get_mitigation_service,
    get_mitigation_summary_service,
    get_penalty_projection_repository,
    parse_include,
)
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import AppError, NotFoundError
from app.models.enums import SummaryStatus
from app.repositories.penalties.mitigation import MitigationOptionRepository
from app.repositories.penalties.projection import PenaltyProjectionRepository
from app.schemas.penalties.mitigations import (
    MitigationOptionDetailResponse,
    MitigationOptionResponse,
    MitigationOptionsResponse,
    PenaltyMitigationSummaryRequest,
    PenaltyMitigationSummaryResponse,
    PenaltyMitigationSummaryStatusResponse,
)
from app.services.penalties.mitigation.service import MitigationService
from app.services.penalties.mitigation.summary_service import MitigationSummaryService

router = APIRouter(tags=["penalty-mitigations"])

_INCLUDE_SUMMARY = parse_include(frozenset({"summary"}))


def _resolve_projection(projections: PenaltyProjectionRepository, projection_id: UUID) -> tuple[UUID, date]:
    row = projections.get_by_id(projection_id)
    if row is None:
        raise NotFoundError(
            code="PROJECTION_NOT_FOUND",
            message=f"No penalty projection found with projection_id={projection_id!r}",
        )
    return row["purchase_order_id"], row["projection_date"]


def _try_get_summary_job(service: MitigationSummaryService, purchase_order_id: UUID, as_of_date: date):
    """Pure read -- see `app.api.v1.penalties.projections`'s sibling helper
    for why any `AppError` (not just `NotFoundError`) here means "nothing
    to include", not the whole request's error."""
    try:
        return service.get_status(purchase_order_id, as_of_date=as_of_date)
    except AppError:
        return None


@router.get("/penalty-mitigations", response_model=Envelope[MitigationOptionsResponse])
def list_penalty_mitigations(
    projection_id: UUID = Query(...),
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
    mitigation_options: MitigationOptionRepository = Depends(get_mitigation_option_repository),
) -> Envelope[MitigationOptionsResponse]:
    purchase_order_id, projection_date = _resolve_projection(projections, projection_id)
    options = mitigation_options.list_for_date(purchase_order_id, projection_date)
    return success_envelope(
        MitigationOptionsResponse(
            purchase_order_id=purchase_order_id,
            projection_date=projection_date,
            options=[MitigationOptionResponse.model_validate(o) for o in options],
        )
    )


@router.post("/penalty-mitigations", response_model=Envelope[MitigationOptionsResponse], status_code=201)
def run_penalty_mitigations(
    projection_id: UUID = Query(...),
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
    mitigation_options: MitigationOptionRepository = Depends(get_mitigation_option_repository),
    mitigation_service: MitigationService = Depends(get_mitigation_service),
) -> Envelope[MitigationOptionsResponse]:
    purchase_order_id, projection_date = _resolve_projection(projections, projection_id)
    resolved_date, _ = mitigation_service.run_for_purchase_order(purchase_order_id, projection_date)
    # `run_for_purchase_order` returns the pure-engine `MitigationOption`
    # dataclasses it just persisted -- no `id`/`purchase_order_id`/
    # `projection_date` (see `app.services.penalties.mitigation.types`),
    # unlike this repository's dict shape (same `projected_penalty_after`
    # field, plus its own surrogate `id`) that `MitigationOptionResponse`
    # is built against. Re-reading the rows `save_results` just wrote keeps
    # this route's response shape consistent with the sibling GET endpoint
    # below, at the cost of one extra read.
    options = mitigation_options.list_for_date(purchase_order_id, resolved_date)
    return success_envelope(
        MitigationOptionsResponse(
            purchase_order_id=purchase_order_id,
            projection_date=resolved_date,
            options=[MitigationOptionResponse.model_validate(o) for o in options],
        ),
        message="Mitigation options computed.",
    )


@router.get(
    "/penalty-mitigations/{mitigation_id}",
    response_model=Envelope[MitigationOptionDetailResponse],
)
def get_penalty_mitigation(
    mitigation_id: UUID,
    mitigation_options: MitigationOptionRepository = Depends(get_mitigation_option_repository),
    mitigation_summary_service: MitigationSummaryService = Depends(get_mitigation_summary_service),
    include: set[str] = Depends(_INCLUDE_SUMMARY),
) -> Envelope[MitigationOptionDetailResponse]:
    """Pure read, never schedules generation (approved plan §5)."""
    row = mitigation_options.get_by_id(mitigation_id)
    if row is None:
        raise NotFoundError(
            code="MITIGATION_OPTION_NOT_FOUND",
            message=f"No mitigation option found with mitigation_id={mitigation_id!r}",
        )
    detail = MitigationOptionDetailResponse.model_validate(row)
    if "summary" in include:
        job = _try_get_summary_job(
            mitigation_summary_service, row["purchase_order_id"], row["projection_date"]
        )
        if job is not None:
            detail.summary_status = SummaryStatus(job.status)
            if job.status == SummaryStatus.READY and job.output is not None:
                detail.summary = PenaltyMitigationSummaryResponse.model_validate(job.output)
    return success_envelope(detail)


@router.post(
    "/purchase-orders/{purchase_order_id}/penalty-mitigations/summary",
    response_model=Envelope[PenaltyMitigationSummaryStatusResponse],
    responses={202: {"model": Envelope[PenaltyMitigationSummaryStatusResponse]}},
)
def trigger_penalty_mitigation_summary(
    purchase_order_id: UUID,
    body: PenaltyMitigationSummaryRequest,
    response: Response,
    mitigation_summary_service: MitigationSummaryService = Depends(get_mitigation_summary_service),
) -> Envelope[PenaltyMitigationSummaryStatusResponse]:
    job = mitigation_summary_service.get_or_schedule(
        purchase_order_id, as_of_date=body.as_of_date, force_regenerate=body.force_regenerate
    )

    if job.status == SummaryStatus.READY:
        assert job.output is not None
        return success_envelope(
            PenaltyMitigationSummaryStatusResponse(
                purchase_order_id=purchase_order_id,
                as_of_date=job.as_of_date,
                status=SummaryStatus.READY,
                summary=PenaltyMitigationSummaryResponse.model_validate(job.output),
            )
        )

    response.status_code = 202
    return success_envelope(
        PenaltyMitigationSummaryStatusResponse(
            purchase_order_id=purchase_order_id,
            as_of_date=job.as_of_date,
            status=SummaryStatus.PENDING,
        ),
        message="Penalty mitigation summary generation queued.",
    )
