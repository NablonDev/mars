"""API endpoints for post-delivery penalty dispute resolution
(`penalties.penalty_dispute`).

Flat resource style (`/penalties/disputes`, not nested under
`/purchase-orders/{id}/...`), matching every other route in this domain
(`app.api.v1.penalties.projections`/`mitigations`) -- see those modules'
docstrings for the full "no 2-3 URL shapes for one resource" rationale.
`.../{dispute_id}/analyze`/`.../{dispute_id}/resolve`/`.../{dispute_id}/summary`
stay nested under the dispute's own id -- sub-actions on one resource, not a
second URL shape for the collection (mirrors
`app/api/v1/common/delivery_change_requests.py`'s
`.../{delivery_change_request_id}/response` convention).

`analyze` is synchronous and returns the analyzed result immediately (no
blocking human-approval gate -- locked design decision); `resolve` records a
human decision on an already-ANALYZED dispute. The summary trigger/poll
pair mirrors `app.api.v1.penalties.projections`'s own summary routes.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import (
    get_dispute_service,
    get_dispute_summary_service,
    get_purchase_order_repository,
)
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import NotFoundError
from app.models.enums import SummaryStatus
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.schemas.penalties.disputes import (
    DisputeOpenRequest,
    DisputeResolveRequest,
    DisputeResponse,
    DisputeSummaryRequest,
    DisputeSummaryResponse,
    DisputeSummaryStatusResponse,
)
from app.services.penalties.dispute.service import DisputeService
from app.services.penalties.dispute.summary_service import DisputeSummaryService

router = APIRouter(tags=["penalty-disputes"])


@router.post(
    "/penalties/disputes",
    response_model=Envelope[DisputeResponse],
    status_code=status.HTTP_201_CREATED,
)
def open_penalty_dispute(
    body: DisputeOpenRequest,
    service: DisputeService = Depends(get_dispute_service),
) -> Envelope[DisputeResponse]:
    created = service.open_dispute(
        body.actual_penalty_id, body.reason_code, body.claimed_amount, notes=body.notes
    )
    return success_envelope(DisputeResponse.model_validate(created), message="Penalty dispute opened.")


@router.get(
    "/penalties/disputes",
    response_model=Envelope[list[DisputeResponse]],
)
def list_penalty_disputes(
    purchase_order_id: UUID | None = Query(default=None),
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    service: DisputeService = Depends(get_dispute_service),
) -> Envelope[list[DisputeResponse]]:
    """`purchase_order_id` given: every dispute for that PO (404 if the PO
    itself doesn't exist). Omitted: every dispute across every PO."""
    if purchase_order_id is not None:
        purchase_orders.require_purchase_order(purchase_order_id)
    rows = [DisputeResponse.model_validate(r) for r in service.list_for_purchase_order(purchase_order_id)]
    return success_envelope(rows)


@router.get(
    "/penalties/disputes/{dispute_id}",
    response_model=Envelope[DisputeResponse],
)
def get_penalty_dispute(
    dispute_id: UUID,
    service: DisputeService = Depends(get_dispute_service),
) -> Envelope[DisputeResponse]:
    return success_envelope(DisputeResponse.model_validate(service.get(dispute_id)))


@router.post(
    "/penalties/disputes/{dispute_id}/analyze",
    response_model=Envelope[DisputeResponse],
)
def analyze_penalty_dispute(
    dispute_id: UUID,
    service: DisputeService = Depends(get_dispute_service),
) -> Envelope[DisputeResponse]:
    """Synchronous: computes and persists the verdict immediately, moving
    the dispute to ANALYZED. No job-queue involvement -- see this module's
    docstring."""
    analyzed = service.analyze(dispute_id)
    return success_envelope(DisputeResponse.model_validate(analyzed), message="Penalty dispute analyzed.")


@router.post(
    "/penalties/disputes/{dispute_id}/resolve",
    response_model=Envelope[DisputeResponse],
)
def resolve_penalty_dispute(
    dispute_id: UUID,
    body: DisputeResolveRequest,
    service: DisputeService = Depends(get_dispute_service),
) -> Envelope[DisputeResponse]:
    resolved = service.resolve(
        dispute_id,
        resolved_by=body.resolved_by,
        override_verdict=body.override_verdict,
        override_reason=body.override_reason,
    )
    return success_envelope(DisputeResponse.model_validate(resolved), message="Penalty dispute resolved.")


@router.post(
    "/penalties/disputes/{dispute_id}/summary",
    response_model=Envelope[DisputeSummaryStatusResponse],
    responses={202: {"model": Envelope[DisputeSummaryStatusResponse]}},
)
def trigger_penalty_dispute_summary(
    dispute_id: UUID,
    body: DisputeSummaryRequest,
    response: Response,
    summary_service: DisputeSummaryService = Depends(get_dispute_summary_service),
) -> Envelope[DisputeSummaryStatusResponse]:
    job = summary_service.get_or_schedule_for_dispute(dispute_id, force_regenerate=body.force_regenerate)

    if job.status == SummaryStatus.READY:
        assert job.output is not None
        return success_envelope(
            DisputeSummaryStatusResponse(
                dispute_id=dispute_id,
                as_of_date=job.as_of_date,
                status=SummaryStatus.READY,
                summary=DisputeSummaryResponse.model_validate(job.output),
            )
        )

    response.status_code = 202
    return success_envelope(
        DisputeSummaryStatusResponse(
            dispute_id=dispute_id, as_of_date=job.as_of_date, status=SummaryStatus.PENDING
        ),
        message="Penalty dispute summary generation queued.",
    )


@router.get(
    "/penalties/disputes/{dispute_id}/summary",
    response_model=Envelope[DisputeSummaryStatusResponse],
)
def get_penalty_dispute_summary(
    dispute_id: UUID,
    summary_service: DisputeSummaryService = Depends(get_dispute_summary_service),
) -> Envelope[DisputeSummaryStatusResponse]:
    """Pure read -- never schedules generation. Mirrors
    `app.api.v1.penalties.projections.get_penalty_projection_summary`'s
    "never-requested is success, not 404" convention."""
    try:
        job = summary_service.get_status_for_dispute(dispute_id)
    except NotFoundError as exc:
        if exc.code != "NO_DISPUTE_SUMMARY_JOB_EXISTS":
            raise
        return success_envelope(
            DisputeSummaryStatusResponse(dispute_id=dispute_id, as_of_date=None, status=None)
        )
    return success_envelope(
        DisputeSummaryStatusResponse(
            dispute_id=dispute_id,
            as_of_date=job.as_of_date,
            status=SummaryStatus(job.status),
            summary=(
                DisputeSummaryResponse.model_validate(job.output)
                if job.status == SummaryStatus.READY and job.output is not None
                else None
            ),
        )
    )
