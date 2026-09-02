"""API endpoints for `penalties.mitigation_option`: compute/list ranked
mitigation options for a projection, and the mitigation-summary
trigger/poll contract.

Mitigation options are keyed by `(purchase_order_id, projection_date)`, not by
their own FK to `penalty_projection` (no such FK exists -- see
`app.models.penalties.mitigation.MitigationOption`'s docstring). Every route
below accepts that pair directly, OR a `projection_id` resolved to the pair
via `_resolve_projection` (`PenaltyProjectionRepository.get_by_id` first) --
kept as a convenience alias, not the only way in.

Routes are flat (`/penalties/mitigations`, not nested under
`/purchase-orders/{id}/...` or `/penalty-projections/{id}/...`) for the same
reason `app.api.v1.penalties.projections` is flat -- see that module's
docstring.

`POST /penalties/mitigations/summary` exists as a trigger endpoint symmetric
to the projection-summary one: without it, `include=summary` below could
never have anything to show.
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
    get_purchase_order_repository,
    parse_include,
)
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.models.enums import SummaryStatus
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.mitigation import MitigationOptionRepository
from app.repositories.penalties.projection import PenaltyProjectionRepository
from app.schemas.penalties.mitigations import (
    MitigationOptionDetailResponse,
    MitigationOptionsResponse,
    PenaltyMitigationRunRequest,
    PenaltyMitigationSummaryRequest,
    PenaltyMitigationSummaryResponse,
    PenaltyMitigationSummaryStatusResponse,
)
from app.services.penalties.mitigation.service import MitigationService
from app.services.penalties.mitigation.summary_service import MitigationSummaryService

router = APIRouter(tags=["penalty-mitigations"])

# Used by the GET routes below (list, get-one). `POST /penalties/mitigations`
# no longer accepts `include=` at all -- a compute call always returns the
# bare mitigation-options result, with `summary` left unset; fetch it via
# the GET routes below instead.
_INCLUDE_SUMMARY = parse_include(frozenset({"summary"}))


def _resolve_projection(projections: PenaltyProjectionRepository, projection_id: UUID) -> tuple[UUID, date]:
    row = projections.get_by_id(projection_id)
    if row is None:
        raise NotFoundError(
            code="PROJECTION_NOT_FOUND",
            message=f"No penalty projection found with projection_id={projection_id}",
        )
    return row["purchase_order_id"], row["projection_date"]


def _resolve_purchase_order_and_date(
    projections: PenaltyProjectionRepository,
    purchase_orders: PurchaseOrderRepository,
    *,
    projection_id: UUID | None,
    purchase_order_id: UUID | None,
    projection_date: date | None,
) -> tuple[UUID, date]:
    """Shared either-or resolution for the GET list route's query params --
    the POST run route enforces the identical "exactly one shape" rule at
    the schema level via `PenaltyMitigationRunRequest`'s model validator;
    query params have no equivalent body-model validator, so it's done here
    instead.

    The direct `purchase_order_id` + `projection_date` shape also confirms
    the purchase order itself exists (`PurchaseOrderRepository.
    require_purchase_order` -- same repository call/`NotFoundError(code=
    "PO_NOT_FOUND")` convention `run_for_purchase_order` already raises on
    the POST/compute routes, and `list_penalty_projections`/
    `get_penalty_exposure` already use on their own GET side) -- otherwise
    an unknown `purchase_order_id` would 200 with an empty `options: []`
    indistinguishable from "no mitigations computed yet for a real PO". The
    `projection_id` shape needs no separate check: `_resolve_projection`
    already 404s on an unknown id, and a resolved projection's
    `purchase_order_id` is a real FK, so it can never reference a
    nonexistent purchase order."""
    has_projection_id = projection_id is not None
    has_purchase_order_id = purchase_order_id is not None
    has_projection_date = projection_date is not None
    if has_purchase_order_id != has_projection_date:
        raise ValidationError(
            code="VALIDATION_ERROR",
            message="purchase_order_id and projection_date must both be provided together, or neither.",
        )
    has_direct_pair = has_purchase_order_id and has_projection_date
    if has_projection_id == has_direct_pair:
        raise ValidationError(
            code="VALIDATION_ERROR",
            message="Provide exactly one of `projection_id` or (`purchase_order_id` + `projection_date`).",
        )
    if projection_id is not None:
        return _resolve_projection(projections, projection_id)
    assert purchase_order_id is not None
    assert projection_date is not None
    purchase_orders.require_purchase_order(purchase_order_id)
    return purchase_order_id, projection_date


def _try_get_summary_job(service: MitigationSummaryService, purchase_order_id: UUID, as_of_date: date | None):
    """Pure read -- see `app.api.v1.penalties.projections`'s sibling helper
    for why any `AppError` (not just `NotFoundError`) here means "nothing
    to include", not the whole request's error."""
    try:
        return service.get_status(purchase_order_id, as_of_date=as_of_date)
    except AppError:
        return None


def _attach_summary(
    detail: MitigationOptionDetailResponse,
    service: MitigationSummaryService,
    purchase_order_id: UUID,
    projection_date: date,
) -> None:
    """Shared by the single-mitigation GET, the list GET, and the run POST
    below -- mirrors `app.api.v1.penalties.projections._attach_summary`'s
    shape exactly."""
    job = _try_get_summary_job(service, purchase_order_id, projection_date)
    if job is None:
        return
    detail.summary_status = SummaryStatus(job.status)
    if job.status == SummaryStatus.READY and job.output is not None:
        detail.summary = PenaltyMitigationSummaryResponse.model_validate(job.output)


@router.get("/penalties/mitigations", response_model=Envelope[MitigationOptionsResponse])
def list_penalty_mitigations(
    projection_id: UUID | None = Query(default=None),
    purchase_order_id: UUID | None = Query(default=None),
    projection_date: date | None = Query(default=None),
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    mitigation_options: MitigationOptionRepository = Depends(get_mitigation_option_repository),
    mitigation_summary_service: MitigationSummaryService = Depends(get_mitigation_summary_service),
    include: set[str] = Depends(_INCLUDE_SUMMARY),
) -> Envelope[MitigationOptionsResponse]:
    resolved_purchase_order_id, resolved_projection_date = _resolve_purchase_order_and_date(
        projections,
        purchase_orders,
        projection_id=projection_id,
        purchase_order_id=purchase_order_id,
        projection_date=projection_date,
    )
    options = mitigation_options.list_for_date(resolved_purchase_order_id, resolved_projection_date)
    details = [MitigationOptionDetailResponse.model_validate(o) for o in options]
    if "summary" in include:
        for detail in details:
            _attach_summary(
                detail, mitigation_summary_service, resolved_purchase_order_id, resolved_projection_date
            )
    return success_envelope(
        MitigationOptionsResponse(
            purchase_order_id=resolved_purchase_order_id,
            projection_date=resolved_projection_date,
            options=details,
        )
    )


@router.post("/penalties/mitigations", response_model=Envelope[MitigationOptionsResponse], status_code=201)
def run_penalty_mitigations(
    body: PenaltyMitigationRunRequest,
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
    mitigation_options: MitigationOptionRepository = Depends(get_mitigation_option_repository),
    mitigation_service: MitigationService = Depends(get_mitigation_service),
) -> Envelope[MitigationOptionsResponse]:
    """Compute mitigation options. Does not accept `include=` -- the
    response never carries `summary`; fetch it separately via
    `GET /penalties/mitigations` (or the get-one route) once the options
    exist."""
    # `PenaltyMitigationRunRequest`'s own model validator already guarantees
    # exactly one of the two shapes was supplied.
    if body.projection_id is not None:
        purchase_order_id, projection_date = _resolve_projection(projections, body.projection_id)
    else:
        assert body.purchase_order_id is not None
        assert body.projection_date is not None
        purchase_order_id, projection_date = body.purchase_order_id, body.projection_date

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
    details = [MitigationOptionDetailResponse.model_validate(o) for o in options]
    return success_envelope(
        MitigationOptionsResponse(
            purchase_order_id=purchase_order_id,
            projection_date=resolved_date,
            options=details,
        ),
        message="Mitigation options computed.",
    )


@router.post(
    "/penalties/mitigations/summary",
    response_model=Envelope[PenaltyMitigationSummaryStatusResponse],
    responses={202: {"model": Envelope[PenaltyMitigationSummaryStatusResponse]}},
)
def trigger_penalty_mitigation_summary(
    body: PenaltyMitigationSummaryRequest,
    response: Response,
    mitigation_summary_service: MitigationSummaryService = Depends(get_mitigation_summary_service),
) -> Envelope[PenaltyMitigationSummaryStatusResponse]:
    job = mitigation_summary_service.get_or_schedule(
        body.purchase_order_id, as_of_date=body.as_of_date, force_regenerate=body.force_regenerate
    )

    if job.status == SummaryStatus.READY:
        assert job.output is not None
        return success_envelope(
            PenaltyMitigationSummaryStatusResponse(
                purchase_order_id=body.purchase_order_id,
                as_of_date=job.as_of_date,
                status=SummaryStatus.READY,
                summary=PenaltyMitigationSummaryResponse.model_validate(job.output),
            )
        )

    response.status_code = 202
    return success_envelope(
        PenaltyMitigationSummaryStatusResponse(
            purchase_order_id=body.purchase_order_id,
            as_of_date=job.as_of_date,
            status=SummaryStatus.PENDING,
        ),
        message="Penalty mitigation summary generation queued.",
    )


@router.get(
    "/penalties/mitigations/summary",
    response_model=Envelope[PenaltyMitigationSummaryStatusResponse],
)
def get_penalty_mitigation_summary(
    purchase_order_id: UUID = Query(...),
    as_of_date: date | None = Query(default=None),
    mitigation_summary_service: MitigationSummaryService = Depends(get_mitigation_summary_service),
) -> Envelope[PenaltyMitigationSummaryStatusResponse]:
    """Dedicated read equivalent of `?include=summary` elsewhere -- lets a
    caller poll a summary job without re-fetching its mitigation options.
    Pure read: never schedules generation, only the POST sibling above can
    enqueue one.

    The genuine "never requested" case is `success: true`, `status: None`,
    `summary: None` -- see `app.api.v1.penalties.projections.
    get_penalty_projection_summary`'s docstring, this route's exact mirror,
    for the full reasoning. A `purchase_order_id` that doesn't correspond
    to any real purchase order, or an `as_of_date` `get_status` itself
    rejects, are genuine caller errors and still propagate as such.
    """
    try:
        job = mitigation_summary_service.get_status(purchase_order_id, as_of_date=as_of_date)
    except NotFoundError as exc:
        if exc.code != "NO_MITIGATION_SUMMARY_JOB_EXISTS":
            raise
        return success_envelope(
            PenaltyMitigationSummaryStatusResponse(
                purchase_order_id=purchase_order_id,
                as_of_date=as_of_date,
                status=None,
            )
        )
    return success_envelope(
        PenaltyMitigationSummaryStatusResponse(
            purchase_order_id=purchase_order_id,
            as_of_date=job.as_of_date,
            status=SummaryStatus(job.status),
            summary=(
                PenaltyMitigationSummaryResponse.model_validate(job.output)
                if job.status == SummaryStatus.READY and job.output is not None
                else None
            ),
        )
    )


# NOTE: `GET /penalties/mitigations/{mitigation_id}` (below) MUST be
# registered after the two literal `/penalties/mitigations/summary` routes
# above -- FastAPI/Starlette matches routes in registration order, and a
# `{mitigation_id}` path parameter greedily matches the literal segment
# `summary` too. Registering it first would silently swallow
# `GET /penalties/mitigations/summary` as a 422 UUID-parse failure on
# `mitigation_id="summary"` instead of ever reaching the dedicated route.
@router.get(
    "/penalties/mitigations/{mitigation_id}",
    response_model=Envelope[MitigationOptionDetailResponse],
)
def get_penalty_mitigation(
    mitigation_id: UUID,
    mitigation_options: MitigationOptionRepository = Depends(get_mitigation_option_repository),
    mitigation_summary_service: MitigationSummaryService = Depends(get_mitigation_summary_service),
    include: set[str] = Depends(_INCLUDE_SUMMARY),
) -> Envelope[MitigationOptionDetailResponse]:
    """Return a mitigation option without triggering summary generation."""
    row = mitigation_options.get_by_id(mitigation_id)
    if row is None:
        raise NotFoundError(
            code="MITIGATION_OPTION_NOT_FOUND",
            message=f"No mitigation option found with mitigation_id={mitigation_id}",
        )
    detail = MitigationOptionDetailResponse.model_validate(row)
    if "summary" in include:
        _attach_summary(detail, mitigation_summary_service, row["purchase_order_id"], row["projection_date"])
    return success_envelope(detail)
