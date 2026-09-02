"""API endpoints for `penalties.penalty_projection`: running/listing/
single-read/exposure, and the projection-summary trigger/poll contract.

Computing a projection inline (`POST /penalties/projections`) and queuing a
batch of projections (`POST /api/v1/job-runs`, `app/api/v1/job_runs.py`)
are two distinct operations -- `GET /penalties/projections?status=open` is
a synchronous list read, not a trigger.

Every route below is flat (`/penalties/...`, not nested under
`/purchase-orders/{id}/...`): a resource with its own globally-meaningful
id, fetched directly and listed cross-parent as a first-class case,
shouldn't have 2-3 different URL shapes for the same resource type. The one
list route (`GET /penalties/projections`) folds what used to be two
separate routes -- a purchase-order-scoped history and a cross-PO open
list -- into one, distinguished by whether `purchase_order_id` is supplied
(see `PenaltyProjectionRepository.list_projections`'s docstring for the
exact semantics preserved from each).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import (
    get_mitigation_option_repository,
    get_mitigation_summary_service,
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
from app.repositories.penalties.mitigation import MitigationOptionRepository
from app.repositories.penalties.projection import PenaltyProjectionRepository
from app.schemas.penalties.mitigations import MitigationOptionResponse, PenaltyMitigationSummaryResponse
from app.schemas.penalties.projections import (
    PenaltyExposureResponse,
    PenaltyProjectionDetailResponse,
    PenaltyProjectionResultResponse,
    PenaltyProjectionRunRequest,
    PenaltyProjectionSummaryRequest,
    PenaltyProjectionSummaryResponse,
    PenaltyProjectionSummaryStatusResponse,
    ViolationResponse,
)
from app.services.penalties.mitigation.summary_service import MitigationSummaryService
from app.services.penalties.projection.service import ProjectionService
from app.services.penalties.projection.summary_service import ProjectionSummaryService

router = APIRouter(tags=["penalty-projections"])

# Uniform across every GET projection route below (list, get-one alike) --
# no route gets a narrower allow-list than another (the bug this pass
# closes: GET history supported all three, GET single supported only
# `summary`). `POST /penalties/projections` no longer accepts `include=` at
# all -- a compute call always returns the bare projection result, with
# `summary`/`mitigations`/`mitigation_summary` left unset; fetch those via
# the GET routes below instead.
_INCLUDE_PROJECTION = parse_include(frozenset({"summary", "mitigations", "mitigation_summary"}))


def _require_projection_id(violation) -> UUID:
    """`ProjectionService.run_for_purchase_order` always stitches the
    persisted `penalty_projection.id` back onto every `ViolationProjection`
    it returns (see that dataclass field's docstring) -- this never
    actually raises for a real engine result; the assertion is a fail-fast
    guard against `_to_result_response` being reused against a
    `ViolationProjection` that skipped that step (e.g. a future caller
    passing `_build_projection_result`'s reconstruction, which leaves `id`
    unset -- see `app.services.penalties.mitigation.service`)."""
    if violation.id is None:
        raise AssertionError(
            f"ViolationProjection for rule_id={violation.rule_id!r} has no persisted id -- "
            "was it run through ProjectionService.run_for_purchase_order?"
        )
    return violation.id


def _to_result_response(result) -> PenaltyProjectionResultResponse:
    return PenaltyProjectionResultResponse(
        purchase_order_id=UUID(result.order_id),
        projection_date=result.projection_date,
        days_to_delivery=result.days_to_delivery,
        shortage_probability=result.shortage_probability,
        delay_probability=result.delay_probability,
        violations=[
            ViolationResponse(
                # `run_for_purchase_order` always stitches this back onto
                # every violation immediately after persisting it -- see
                # `ProjectionService.run_for_purchase_order` and
                # `ViolationProjection.id`'s docstring; never `None` here.
                projection_id=_require_projection_id(v),
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


def _try_get_summary_job(
    service: ProjectionSummaryService,
    purchase_order_id: UUID,
    as_of_date: date | None,
):
    """Pure read: resolve the cached summary job for one (PO, date), or
    `None` when no summary can be shown for it -- either none was ever
    requested (`NotFoundError`), or the date itself can never have had one
    (e.g. a forward-looking projection date still in the future relative to
    "today" -- `ValidationError(code="INVALID_AS_OF_DATE")`, a completely
    normal case for this domain's projections, not a caller error). Never
    schedules generation (the `?include=` semantics below); any
    `AppError` here is treated as "nothing to include," not surfaced as the
    whole request's error. `as_of_date=None` is forwarded as-is --
    `ProjectionSummaryService.get_status` defaults it to "today" itself.
    """
    try:
        return service.get_status(purchase_order_id, as_of_date=as_of_date)
    except AppError:
        return None


class _SummaryAttachable(Protocol):
    """Structural shape `_attach_summary`/`_attach_mitigations`/
    `_attach_mitigation_summary` need -- both `PenaltyProjectionDetailResponse`
    (the GET `?include=` shape) and `PenaltyProjectionResultResponse` (the
    POST run-result shape) satisfy this without either inheriting from the
    other; keeps these helpers shared instead of duplicated per response
    model."""

    projection_date: date
    summary_status: SummaryStatus | None
    summary: PenaltyProjectionSummaryResponse | None
    mitigations: list[MitigationOptionResponse] | None
    mitigation_summary_status: SummaryStatus | None
    mitigation_summary: PenaltyMitigationSummaryResponse | None


def _attach_summary(
    row: _SummaryAttachable,
    service: ProjectionSummaryService,
    purchase_order_id: UUID,
) -> None:
    job = _try_get_summary_job(service, purchase_order_id, row.projection_date)
    if job is None:
        return
    row.summary_status = SummaryStatus(job.status)
    if job.status == SummaryStatus.READY and job.output is not None:
        row.summary = PenaltyProjectionSummaryResponse.model_validate(job.output)


def _try_get_mitigation_summary_job(
    service: MitigationSummaryService,
    purchase_order_id: UUID,
    as_of_date: date | None,
):
    """Sibling of this module's own `_try_get_summary_job` (see that
    function's docstring), against `MitigationSummaryService` instead of
    `ProjectionSummaryService` -- same "any `AppError` here just means
    nothing to include" contract. Also mirrors
    `app.api.v1.penalties.mitigations._try_get_summary_job`, kept as its own
    small copy rather than imported cross-router for the same reason that
    module's helper already cites its projections-module sibling instead of
    importing it.
    """
    try:
        return service.get_status(purchase_order_id, as_of_date=as_of_date)
    except AppError:
        return None


def _attach_mitigations(
    row: _SummaryAttachable,
    mitigation_options: MitigationOptionRepository,
    purchase_order_id: UUID,
) -> None:
    """`include=mitigations` -- same per-row, keyed-on-`row.projection_date`
    embedding shape as `_attach_summary` above. Reuses
    `MitigationOptionRepository.list_for_date`, the exact lookup
    `app.api.v1.penalties.mitigations.list_penalty_mitigations` already uses
    once it has resolved a `(purchase_order_id, projection_date)` pair --
    here that pair is already on hand per row, no `projection_id` resolution
    needed. A row with no computed mitigation options yet is left `None`
    (legitimate empty state, not an error)."""
    options = mitigation_options.list_for_date(purchase_order_id, row.projection_date)
    if options:
        row.mitigations = [MitigationOptionResponse.model_validate(o) for o in options]


def _attach_mitigation_summary(
    row: _SummaryAttachable,
    service: MitigationSummaryService,
    purchase_order_id: UUID,
) -> None:
    """`include=mitigation_summary` -- same per-row embedding shape as
    `_attach_summary` above, against the mitigation-summary job instead of
    the projection-summary job."""
    job = _try_get_mitigation_summary_job(service, purchase_order_id, row.projection_date)
    if job is None:
        return
    row.mitigation_summary_status = SummaryStatus(job.status)
    if job.status == SummaryStatus.READY and job.output is not None:
        row.mitigation_summary = PenaltyMitigationSummaryResponse.model_validate(job.output)


@router.post(
    "/penalties/projections",
    response_model=Envelope[PenaltyProjectionResultResponse],
    status_code=201,
)
def run_penalty_projection(
    body: PenaltyProjectionRunRequest,
    projection_service: ProjectionService = Depends(get_projection_service),
) -> Envelope[PenaltyProjectionResultResponse]:
    """Compute a penalty projection. Does not accept `include=` -- the
    response never carries `summary`/`mitigations`/`mitigation_summary`;
    fetch those separately via `GET /penalties/projections/{projection_id}`
    (or the list route) once the projection exists."""
    result = projection_service.run_for_purchase_order(
        body.purchase_order_id, body.projection_date, body.stacking_mode_override
    )
    response = _to_result_response(result)
    return success_envelope(response, message="Penalty projection computed.")


@router.get(
    "/penalties/projections",
    response_model=Envelope[list[PenaltyProjectionDetailResponse]],
)
def list_penalty_projections(
    purchase_order_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    projection_date: date | None = Query(default=None),
    projection_date_from: date | None = Query(default=None),
    projection_date_to: date | None = Query(default=None),
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
    projection_summary_service: ProjectionSummaryService = Depends(get_projection_summary_service),
    mitigation_options: MitigationOptionRepository = Depends(get_mitigation_option_repository),
    mitigation_summary_service: MitigationSummaryService = Depends(get_mitigation_summary_service),
    include: set[str] = Depends(_INCLUDE_PROJECTION),
) -> Envelope[list[PenaltyProjectionDetailResponse]]:
    """Merges the old purchase-order-scoped history route and the old
    cross-PO open-projection list into one flat, filterable route.

    `purchase_order_id` given: identical to the old nested history route --
    every persisted projection row for that PO (404 if the PO itself
    doesn't exist), `status`/date filters narrowing further.

    `purchase_order_id` omitted: identical to the old cross-PO
    `GET /penalty-projections?status=` list -- defaults `status` to `"OPEN"`
    when not supplied, same as that route's own default.
    """
    if purchase_order_id is not None:
        purchase_orders.require_purchase_order(purchase_order_id)
        resolved_status = status.upper() if status is not None else None
    else:
        resolved_status = status.upper() if status is not None else "OPEN"

    rows = [
        PenaltyProjectionDetailResponse.model_validate(r)
        for r in projections.list_projections(
            purchase_order_id=purchase_order_id,
            status=resolved_status,
            projection_date=projection_date,
            projection_date_from=projection_date_from,
            projection_date_to=projection_date_to,
        )
    ]
    if "summary" in include:
        for row in rows:
            _attach_summary(row, projection_summary_service, row.purchase_order_id)
    if "mitigations" in include:
        for row in rows:
            _attach_mitigations(row, mitigation_options, row.purchase_order_id)
    if "mitigation_summary" in include:
        for row in rows:
            _attach_mitigation_summary(row, mitigation_summary_service, row.purchase_order_id)
    return success_envelope(rows)


@router.post(
    "/penalties/projections/summary",
    response_model=Envelope[PenaltyProjectionSummaryStatusResponse],
    responses={202: {"model": Envelope[PenaltyProjectionSummaryStatusResponse]}},
)
def trigger_penalty_projection_summary(
    body: PenaltyProjectionSummaryRequest,
    response: Response,
    projection_summary_service: ProjectionSummaryService = Depends(get_projection_summary_service),
) -> Envelope[PenaltyProjectionSummaryStatusResponse]:
    job = projection_summary_service.get_or_schedule(
        body.purchase_order_id, as_of_date=body.as_of_date, force_regenerate=body.force_regenerate
    )

    if job.status == SummaryStatus.READY:
        assert job.output is not None
        return success_envelope(
            PenaltyProjectionSummaryStatusResponse(
                purchase_order_id=body.purchase_order_id,
                as_of_date=job.as_of_date,
                status=SummaryStatus.READY,
                summary=PenaltyProjectionSummaryResponse.model_validate(job.output),
            )
        )

    # PENDING: get_or_schedule already durably enqueued a process.job_run/
    # job_item (and committed) -- see ProjectionSummaryService._enqueue_
    # regeneration_job. Poll via
    # GET /penalties/projections/summary?purchase_order_id=&as_of_date= (or
    # GET /penalties/projections/{projection_id}?include=summary) for the
    # result.
    response.status_code = 202
    return success_envelope(
        PenaltyProjectionSummaryStatusResponse(
            purchase_order_id=body.purchase_order_id,
            as_of_date=job.as_of_date,
            status=SummaryStatus.PENDING,
        ),
        message="Penalty projection summary generation queued.",
    )


@router.get(
    "/penalties/projections/summary",
    response_model=Envelope[PenaltyProjectionSummaryStatusResponse],
)
def get_penalty_projection_summary(
    purchase_order_id: UUID = Query(...),
    as_of_date: date | None = Query(default=None),
    projection_summary_service: ProjectionSummaryService = Depends(get_projection_summary_service),
) -> Envelope[PenaltyProjectionSummaryStatusResponse]:
    """Dedicated read equivalent of `?include=summary` elsewhere -- lets a
    caller poll a summary job without re-fetching its projection. Pure read:
    never schedules generation, only the POST sibling above can enqueue one.

    The genuine "never requested" case -- no projection-summary job exists
    yet for this `purchase_order_id` at all -- is `success: true`,
    `status: None`, `summary: None`, matching the `?include=summary`
    convention used everywhere else in this module (`_attach_summary`:
    `summary_status=None` is a normal state, never a 404). This route used
    to raise `NotFoundError(code="NO_PROJECTION_SUMMARY_JOB_EXISTS")` for
    that case instead; converged for internal consistency -- a caller
    polling this route for a PO that simply hasn't had a summary requested
    yet shouldn't have to special-case a 404 differently from every other
    "nothing to show" read in this API. A `purchase_order_id` that doesn't
    correspond to any real purchase order (`PO_NOT_FOUND`), or an
    `as_of_date` `get_status` itself rejects (`NO_PROJECTION_EXISTS`,
    `INVALID_AS_OF_DATE`), are genuine caller errors and still propagate as
    such -- only the specific "no job on record" `NotFoundError` is
    downgraded to a null, successful result.
    """
    try:
        job = projection_summary_service.get_status(purchase_order_id, as_of_date=as_of_date)
    except NotFoundError as exc:
        if exc.code != "NO_PROJECTION_SUMMARY_JOB_EXISTS":
            raise
        return success_envelope(
            PenaltyProjectionSummaryStatusResponse(
                purchase_order_id=purchase_order_id,
                as_of_date=as_of_date,
                status=None,
            )
        )
    return success_envelope(
        PenaltyProjectionSummaryStatusResponse(
            purchase_order_id=purchase_order_id,
            as_of_date=job.as_of_date,
            status=SummaryStatus(job.status),
            summary=(
                PenaltyProjectionSummaryResponse.model_validate(job.output)
                if job.status == SummaryStatus.READY and job.output is not None
                else None
            ),
        )
    )


# NOTE: `GET /penalties/projections/{projection_id}` (below) MUST be
# registered after the two literal `/penalties/projections/summary` routes
# above -- FastAPI/Starlette matches routes in registration order, and a
# `{projection_id}` path parameter greedily matches the literal segment
# `summary` too. Registering it first would silently swallow
# `GET /penalties/projections/summary` as a 422 UUID-parse failure on
# `projection_id="summary"` instead of ever reaching the dedicated route.
@router.get(
    "/penalties/projections/{projection_id}",
    response_model=Envelope[PenaltyProjectionDetailResponse],
)
def get_penalty_projection(
    projection_id: UUID,
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
    projection_summary_service: ProjectionSummaryService = Depends(get_projection_summary_service),
    mitigation_options: MitigationOptionRepository = Depends(get_mitigation_option_repository),
    mitigation_summary_service: MitigationSummaryService = Depends(get_mitigation_summary_service),
    include: set[str] = Depends(_INCLUDE_PROJECTION),
) -> Envelope[PenaltyProjectionDetailResponse]:
    """Return a penalty projection without triggering summary generation."""
    row = projections.get_by_id(projection_id)
    if row is None:
        raise NotFoundError(
            code="PROJECTION_NOT_FOUND",
            message=f"No penalty projection found with projection_id={projection_id}",
        )
    detail = PenaltyProjectionDetailResponse.model_validate(row)
    if "summary" in include:
        _attach_summary(detail, projection_summary_service, row["purchase_order_id"])
    if "mitigations" in include:
        _attach_mitigations(detail, mitigation_options, row["purchase_order_id"])
    if "mitigation_summary" in include:
        _attach_mitigation_summary(detail, mitigation_summary_service, row["purchase_order_id"])
    return success_envelope(detail)


@router.get(
    "/penalties/exposure",
    response_model=Envelope[PenaltyExposureResponse],
)
def get_penalty_exposure(
    purchase_order_id: UUID = Query(...),
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    projections: PenaltyProjectionRepository = Depends(get_penalty_projection_repository),
) -> Envelope[PenaltyExposureResponse]:
    purchase_orders.require_purchase_order(purchase_order_id)
    latest = projections.get_latest(purchase_order_id)
    if latest is None:
        raise NotFoundError(
            code="NO_PROJECTION_EXISTS",
            message=f"No projections exist yet for purchase_order_id={purchase_order_id}",
        )
    return success_envelope(PenaltyExposureResponse.model_validate(latest))
