"""API endpoints for `penalties.actual_penalty` -- the realized, post-delivery
penalty a purchase order actually incurred, as distinct from a penalty
projection (forecast) or a penalty mitigation (action taken to reduce one).

Every route below is flat (`/penalties/actual-penalties`, not nested under
`/purchase-orders/{purchase_order_id}/...`) -- same "no 2-3 different URL
shapes for one resource" reasoning as `app.api.v1.penalties.projections`/
`mitigations`; see that module's docstring for the full rationale.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_actual_penalty_repository, get_purchase_order_repository
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import NotFoundError
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.projection import ActualPenaltyRepository
from app.schemas.common.fulfillment import ActualPenaltyRequest, ActualPenaltyResponse

router = APIRouter(tags=["actual-penalties"])


@router.post(
    "/penalties/actual-penalties",
    response_model=Envelope[ActualPenaltyResponse],
    status_code=status.HTTP_201_CREATED,
)
def add_actual_penalty(
    body: ActualPenaltyRequest,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    actual_penalties: ActualPenaltyRepository = Depends(get_actual_penalty_repository),
) -> Envelope[ActualPenaltyResponse]:
    purchase_orders.require_purchase_order(body.purchase_order_id)
    created = actual_penalties.add_actual_penalty(**body.model_dump())
    return success_envelope(ActualPenaltyResponse.model_validate(created), message="Actual penalty recorded.")


@router.get(
    "/penalties/actual-penalties",
    response_model=Envelope[list[ActualPenaltyResponse]],
)
def list_actual_penalties(
    purchase_order_id: UUID | None = Query(default=None),
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    actual_penalties: ActualPenaltyRepository = Depends(get_actual_penalty_repository),
) -> Envelope[list[ActualPenaltyResponse]]:
    """`purchase_order_id` given: every actual penalty for that PO (404 if
    the PO itself doesn't exist) -- unchanged from the old nested route.
    Omitted: every actual penalty across every PO."""
    if purchase_order_id is not None:
        purchase_orders.require_purchase_order(purchase_order_id)
    rows = actual_penalties.list_actual_penalties(purchase_order_id=purchase_order_id)
    return success_envelope([ActualPenaltyResponse.model_validate(r) for r in rows])


# NOTE: unlike `app.api.v1.penalties.projections`/`mitigations`, there is no
# literal `/penalties/actual-penalties/<something>` sibling route (no
# `/summary` action here) -- `{actual_penalty_id}` only ever competes for a
# path one segment longer than `GET /penalties/actual-penalties` itself, so
# registration order can't swallow a literal route the way it could there.
# Still registered after the two routes above, for readability/consistency.
@router.get(
    "/penalties/actual-penalties/{actual_penalty_id}",
    response_model=Envelope[ActualPenaltyResponse],
)
def get_actual_penalty(
    actual_penalty_id: UUID,
    actual_penalties: ActualPenaltyRepository = Depends(get_actual_penalty_repository),
) -> Envelope[ActualPenaltyResponse]:
    """Standalone fetch by the row's own surrogate id."""
    row = actual_penalties.get(actual_penalty_id)
    if row is None:
        raise NotFoundError(
            code="ACTUAL_PENALTY_NOT_FOUND",
            message=f"No actual penalty found with actual_penalty_id={actual_penalty_id}",
        )
    return success_envelope(ActualPenaltyResponse.model_validate(row))
