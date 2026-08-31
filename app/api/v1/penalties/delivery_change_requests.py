"""API endpoints for the PO delivery-date change request/response lifecycle
(`penalties.po_delivery_change_request`).

Was `app/api/v1/fine_projection/po_delivery_change_requests.py` -- nested
entirely under `/purchase-orders/{purchase_order_id}/...` per the approved
plan §5 (was a flat `/po-delivery-change-requests/...` path)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_delivery_change_request_service, get_purchase_order_repository
from app.core.envelope import Envelope, success_envelope
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.schemas.penalties.delivery_change_requests import (
    DeliveryChangeRequestCreate,
    DeliveryChangeRequestResponse,
    DeliveryChangeResponseRequest,
)
from app.services.penalties.delivery_change import PoDeliveryChangeRequestService

router = APIRouter(tags=["po-delivery-change-requests"])


@router.post(
    "/purchase-orders/{purchase_order_id}/delivery-change-requests",
    response_model=Envelope[DeliveryChangeRequestResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_delivery_change_request(
    purchase_order_id: UUID,
    body: DeliveryChangeRequestCreate,
    service: PoDeliveryChangeRequestService = Depends(get_delivery_change_request_service),
) -> Envelope[DeliveryChangeRequestResponse]:
    created = service.create_request(
        purchase_order_id,
        body.reason_code,
        body.proposed_delivery_date,
        notes=body.notes,
    )
    return success_envelope(
        DeliveryChangeRequestResponse.model_validate(created), message="Delivery-change request created."
    )


@router.get(
    "/purchase-orders/{purchase_order_id}/delivery-change-requests",
    response_model=Envelope[list[DeliveryChangeRequestResponse]],
)
def list_delivery_change_requests(
    purchase_order_id: UUID,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    service: PoDeliveryChangeRequestService = Depends(get_delivery_change_request_service),
) -> Envelope[list[DeliveryChangeRequestResponse]]:
    purchase_orders.require_purchase_order(purchase_order_id)
    rows = [DeliveryChangeRequestResponse.model_validate(r) for r in service.list_history(purchase_order_id)]
    return success_envelope(rows)


@router.post(
    "/purchase-orders/{purchase_order_id}/delivery-change-requests/{request_id}/response",
    response_model=Envelope[DeliveryChangeRequestResponse],
)
def record_delivery_change_response(
    purchase_order_id: UUID,
    request_id: str,
    body: DeliveryChangeResponseRequest,
    service: PoDeliveryChangeRequestService = Depends(get_delivery_change_request_service),
) -> Envelope[DeliveryChangeRequestResponse]:
    updated = service.record_response(
        request_id,
        body.decision,
        countered_delivery_date=body.countered_delivery_date,
    )
    return success_envelope(
        DeliveryChangeRequestResponse.model_validate(updated), message="Delivery-change response recorded."
    )
