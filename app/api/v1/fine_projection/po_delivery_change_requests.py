"""API endpoints for the PO delivery-date change request/response lifecycle."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_order_repository, get_po_delivery_change_request_service
from app.repositories.order import OrderRepository
from app.schemas.fine_projection.po_delivery_change_requests import (
    PoDeliveryChangeRequestCreate,
    PoDeliveryChangeRequestResponse,
    PoDeliveryChangeResponseRequest,
)
from app.services.fine_projection.po_delivery_change import PoDeliveryChangeRequestService

router = APIRouter(tags=["po-delivery-change-requests"])


@router.post(
    "/orders/{order_id}/po-delivery-change-requests",
    response_model=PoDeliveryChangeRequestResponse,
    status_code=201,
)
def create_po_delivery_change_request(
    order_id: str,
    body: PoDeliveryChangeRequestCreate,
    service: PoDeliveryChangeRequestService = Depends(get_po_delivery_change_request_service),
) -> dict:
    return service.create_request(
        order_id,
        body.reason_code,
        body.proposed_delivery_date,
        notes=body.notes,
    )


@router.post(
    "/po-delivery-change-requests/{request_id}/response",
    response_model=PoDeliveryChangeRequestResponse,
)
def record_po_delivery_change_response(
    request_id: str,
    body: PoDeliveryChangeResponseRequest,
    service: PoDeliveryChangeRequestService = Depends(get_po_delivery_change_request_service),
) -> dict:
    return service.record_response(
        request_id,
        body.decision,
        countered_delivery_date=body.countered_delivery_date,
    )


@router.get(
    "/orders/{order_id}/po-delivery-change-requests",
    response_model=list[PoDeliveryChangeRequestResponse],
)
def get_po_delivery_change_request_history(
    order_id: str,
    orders: OrderRepository = Depends(get_order_repository),
    service: PoDeliveryChangeRequestService = Depends(get_po_delivery_change_request_service),
) -> list[dict]:
    orders.require_order(order_id)
    return service.list_history(order_id)
