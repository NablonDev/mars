"""API endpoints for the PO delivery-date change request/response lifecycle
(`po_delivery_change_request`, common/unqualified schema).

Create/list/get-by-id are flat (`/delivery-change-requests`, not nested
under `/purchase-orders/{purchase_order_id}/...`) -- same "no 2-3 different
URL shapes for one resource" reasoning as
`app.api.v1.penalties.projections`/`mitigations`; see that module's
docstring for the full rationale. `POST .../{delivery_change_request_id}/response`
stays nested under the request's own `id` -- it's already a sub-action on
one resource's own id (mirrors `app/api/v1/workflow_threads.py`'s own
`/{thread_id}/decisions` convention), not a second URL shape for the same
collection, so it isn't flattened further.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_delivery_change_request_service, get_purchase_order_repository
from app.core.envelope import Envelope, success_envelope
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.schemas.common.delivery_change_requests import (
    DeliveryChangeRequestCreate,
    DeliveryChangeRequestResponse,
    DeliveryChangeResponseRequest,
)
from app.services.penalties.delivery_change import PoDeliveryChangeRequestService

router = APIRouter(tags=["po-delivery-change-requests"])


@router.post(
    "/delivery-change-requests",
    response_model=Envelope[DeliveryChangeRequestResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_delivery_change_request(
    body: DeliveryChangeRequestCreate,
    service: PoDeliveryChangeRequestService = Depends(get_delivery_change_request_service),
) -> Envelope[DeliveryChangeRequestResponse]:
    created = service.create_request(
        body.purchase_order_id,
        body.reason_code,
        body.proposed_delivery_date,
        notes=body.notes,
    )
    return success_envelope(
        DeliveryChangeRequestResponse.model_validate(created), message="Delivery-change request created."
    )


@router.get(
    "/delivery-change-requests",
    response_model=Envelope[list[DeliveryChangeRequestResponse]],
)
def list_delivery_change_requests(
    purchase_order_id: UUID | None = Query(default=None),
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    service: PoDeliveryChangeRequestService = Depends(get_delivery_change_request_service),
) -> Envelope[list[DeliveryChangeRequestResponse]]:
    """`purchase_order_id` given: that PO's full request history (404 if the
    PO itself doesn't exist) -- unchanged from the old nested route.
    Omitted: every request across every PO."""
    if purchase_order_id is not None:
        purchase_orders.require_purchase_order(purchase_order_id)
    rows = [DeliveryChangeRequestResponse.model_validate(r) for r in service.list_history(purchase_order_id)]
    return success_envelope(rows)


# NOTE: `GET /delivery-change-requests/{delivery_change_request_id}` (below)
# MUST be registered after the `POST/GET /delivery-change-requests` routes
# above (they're a different path shape, so no ambiguity there) -- but does
# need to be registered before nothing else here, since the only other route
# under this prefix is the longer `.../{delivery_change_request_id}/response`
# sub-action, which always has one more path segment and so can never be
# shadowed by `{delivery_change_request_id}` matching greedily. Kept in this
# order (get-by-id before the response sub-action) for readability, mirroring
# the literal-before-dynamic ordering `app.api.v1.penalties.projections`/
# `mitigations` need for their own `/summary` sibling route.
@router.get(
    "/delivery-change-requests/{delivery_change_request_id}",
    response_model=Envelope[DeliveryChangeRequestResponse],
)
def get_delivery_change_request(
    delivery_change_request_id: UUID,
    service: PoDeliveryChangeRequestService = Depends(get_delivery_change_request_service),
) -> Envelope[DeliveryChangeRequestResponse]:
    """Standalone fetch by the surrogate `id`."""
    row = service.get_by_id(delivery_change_request_id)
    return success_envelope(DeliveryChangeRequestResponse.model_validate(row))


@router.post(
    "/delivery-change-requests/{delivery_change_request_id}/response",
    response_model=Envelope[DeliveryChangeRequestResponse],
)
def record_delivery_change_response(
    delivery_change_request_id: UUID,
    body: DeliveryChangeResponseRequest,
    service: PoDeliveryChangeRequestService = Depends(get_delivery_change_request_service),
) -> Envelope[DeliveryChangeRequestResponse]:
    updated = service.record_response(
        delivery_change_request_id,
        body.decision,
        countered_delivery_date=body.countered_delivery_date,
    )
    return success_envelope(
        DeliveryChangeRequestResponse.model_validate(updated), message="Delivery-change response recorded."
    )
