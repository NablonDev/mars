"""API endpoints for `common.purchase_order`/`purchase_order_line` and their
fulfillment facts (confirmations, shipments, demand exceptions, actual
penalties).

Was `app/api/v1/orders.py` + `app/api/v1/fine_projection/facts.py` -- header/
line kept split per the ERP redesign; every fact below defaults to the PO's
first line when a specific `purchase_order_line_id` isn't given in the
request, the same single-primary-line convenience
`ProjectionService.build_snapshot` and the seed data already use for a
PO with exactly one line (the only case any worked example exercises).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import (
    get_actual_penalty_repository,
    get_fulfillment_repository,
    get_purchase_order_repository,
)
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import ValidationError
from app.repositories.common.fulfillment import FulfillmentRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.projection import ActualPenaltyRepository
from app.schemas.common.fulfillment import (
    ActualPenaltyRequest,
    ActualPenaltyResponse,
    DemandExceptionRequest,
    DemandExceptionResponse,
    OrderConfirmationLineResponse,
    OrderConfirmationRequest,
    OrderConfirmationResponse,
    ShipmentRequest,
    ShipmentResponse,
)
from app.schemas.common.purchase_orders import (
    PurchaseOrderLineResponse,
    PurchaseOrderRequest,
    PurchaseOrderResponse,
)

router = APIRouter(tags=["purchase-orders"])


def _to_response(purchase_orders: PurchaseOrderRepository, po: dict) -> PurchaseOrderResponse:
    lines = [PurchaseOrderLineResponse.model_validate(line) for line in purchase_orders.list_lines(po["id"])]
    return PurchaseOrderResponse.model_validate({**po, "lines": lines})


def _first_line_id(purchase_orders: PurchaseOrderRepository, purchase_order_id: UUID) -> UUID:
    lines = purchase_orders.list_lines(purchase_order_id)
    if not lines:
        raise ValidationError(
            code="PO_HAS_NO_LINES",
            message=(
                f"Purchase order {purchase_order_id!r} has no lines -- add one first, or pass an "
                "explicit purchase_order_line_id."
            ),
        )
    return lines[0]["id"]


@router.post(
    "/purchase-orders",
    response_model=Envelope[PurchaseOrderResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_purchase_order(
    body: PurchaseOrderRequest,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
) -> Envelope[PurchaseOrderResponse]:
    fields = body.model_dump(exclude={"lines"})
    purchase_order_number = fields.pop("purchase_order_number")
    po = purchase_orders.create_purchase_order(purchase_order_number, **fields)
    for line in body.lines:
        purchase_orders.add_line(purchase_order_id=po["id"], **line.model_dump())
    return success_envelope(_to_response(purchase_orders, po), message="Purchase order created.")


@router.get("/purchase-orders", response_model=Envelope[list[PurchaseOrderResponse]])
def list_purchase_orders(
    order_status: str | None = Query(default=None),
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
) -> Envelope[list[PurchaseOrderResponse]]:
    rows = [_to_response(purchase_orders, po) for po in purchase_orders.list_purchase_orders(order_status)]
    return success_envelope(rows)


# ---------------------------------------------------------------------------
# Order confirmations
# ---------------------------------------------------------------------------


@router.post(
    "/purchase-orders/{purchase_order_id}/confirmations",
    response_model=Envelope[OrderConfirmationResponse],
    status_code=status.HTTP_201_CREATED,
)
def add_confirmation(
    purchase_order_id: UUID,
    body: OrderConfirmationRequest,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    fulfillment: FulfillmentRepository = Depends(get_fulfillment_repository),
) -> Envelope[OrderConfirmationResponse]:
    purchase_orders.require_purchase_order(purchase_order_id)

    confirmation = fulfillment.add_order_confirmation(
        confirmation_number=body.confirmation_number,
        purchase_order_id=purchase_order_id,
        confirmation_date=body.confirmation_date,
        status=body.status,
    )

    # No default line here (unlike demand-exceptions/shipments below):
    # a confirmation always carries a real confirmed_quantity per line, and
    # guessing "the PO's ordered_quantity" would silently invent a fact
    # instead of recording one. Callers must pass `lines` explicitly.
    lines = [
        fulfillment.add_order_confirmation_line(
            order_confirmation_id=confirmation["id"],
            purchase_order_line_id=line.purchase_order_line_id,
            confirmed_quantity=line.confirmed_quantity,
            confirmed_delivery_date=line.confirmed_delivery_date,
            cut_reason_code=line.cut_reason_code,
        )
        for line in body.lines
    ]
    return success_envelope(
        OrderConfirmationResponse.model_validate({**confirmation, "lines": lines}),
        message="Confirmation recorded.",
    )


@router.get(
    "/purchase-orders/{purchase_order_id}/confirmations",
    response_model=Envelope[list[OrderConfirmationLineResponse]],
)
def list_confirmations(
    purchase_order_id: UUID,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    fulfillment: FulfillmentRepository = Depends(get_fulfillment_repository),
) -> Envelope[list[OrderConfirmationLineResponse]]:
    """Flat, per-line confirmation history across every line of the PO.

    `FulfillmentRepository` has no "list every confirmation for a PO"
    method (only per-line lookups) -- this aggregates across the PO's own
    lines rather than returning nested confirmation headers.
    """
    purchase_orders.require_purchase_order(purchase_order_id)
    rows = [
        row
        for line in purchase_orders.list_lines(purchase_order_id)
        for row in fulfillment.list_confirmation_lines_for_line(line["id"])
    ]
    return success_envelope([OrderConfirmationLineResponse.model_validate(r) for r in rows])


# ---------------------------------------------------------------------------
# Shipments (via a per-PO delivery header)
# ---------------------------------------------------------------------------


@router.post(
    "/purchase-orders/{purchase_order_id}/shipments",
    response_model=Envelope[ShipmentResponse],
    status_code=status.HTTP_201_CREATED,
)
def record_shipment(
    purchase_order_id: UUID,
    body: ShipmentRequest,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    fulfillment: FulfillmentRepository = Depends(get_fulfillment_repository),
) -> Envelope[ShipmentResponse]:
    purchase_order = purchase_orders.require_purchase_order(purchase_order_id)

    delivery_number = body.delivery_number or f"DELIV-{purchase_order['purchase_order_number']}"
    delivery = fulfillment.add_delivery(delivery_number=delivery_number, purchase_order_id=purchase_order_id)

    shipment = fulfillment.add_shipment(
        shipment_number=body.shipment_number,
        delivery_id=delivery["id"],
        recorded_at=body.recorded_at,
        carrier_id=body.carrier_id,
        expected_ship_date=body.expected_ship_date,
        actual_ship_date=body.actual_ship_date,
        expected_delivery_date=body.expected_delivery_date,
        actual_delivery_date=body.actual_delivery_date,
        expected_transit_days=body.expected_transit_days,
        appointment_status=body.appointment_status,
        shipment_status=body.shipment_status,
    )
    return success_envelope(ShipmentResponse.model_validate(shipment), message="Shipment recorded.")


@router.get(
    "/purchase-orders/{purchase_order_id}/shipments",
    response_model=Envelope[list[ShipmentResponse]],
)
def list_shipments(
    purchase_order_id: UUID,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    fulfillment: FulfillmentRepository = Depends(get_fulfillment_repository),
) -> Envelope[list[ShipmentResponse]]:
    purchase_orders.require_purchase_order(purchase_order_id)
    rows = fulfillment.list_shipments_for_purchase_order(purchase_order_id)
    return success_envelope([ShipmentResponse.model_validate(r) for r in rows])


# ---------------------------------------------------------------------------
# Demand exceptions
# ---------------------------------------------------------------------------


@router.post(
    "/purchase-orders/{purchase_order_id}/demand-exceptions",
    response_model=Envelope[DemandExceptionResponse],
    status_code=status.HTTP_201_CREATED,
)
def add_demand_exception(
    purchase_order_id: UUID,
    body: DemandExceptionRequest,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    fulfillment: FulfillmentRepository = Depends(get_fulfillment_repository),
) -> Envelope[DemandExceptionResponse]:
    purchase_orders.require_purchase_order(purchase_order_id)
    line_id = body.purchase_order_line_id or _first_line_id(purchase_orders, purchase_order_id)

    created = fulfillment.add_demand_exception(
        exception_id=body.exception_id,
        purchase_order_line_id=line_id,
        flagged_date=body.flagged_date,
    )
    return success_envelope(
        DemandExceptionResponse.model_validate(created), message="Demand exception recorded."
    )


@router.get(
    "/purchase-orders/{purchase_order_id}/demand-exceptions",
    response_model=Envelope[list[DemandExceptionResponse]],
)
def list_demand_exceptions(
    purchase_order_id: UUID,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    fulfillment: FulfillmentRepository = Depends(get_fulfillment_repository),
) -> Envelope[list[DemandExceptionResponse]]:
    purchase_orders.require_purchase_order(purchase_order_id)
    rows = [
        row
        for line in purchase_orders.list_lines(purchase_order_id)
        for row in fulfillment.list_demand_exceptions_for_line(line["id"])
    ]
    return success_envelope([DemandExceptionResponse.model_validate(r) for r in rows])


# ---------------------------------------------------------------------------
# Actual (post-delivery) penalties
# ---------------------------------------------------------------------------


@router.post(
    "/purchase-orders/{purchase_order_id}/actual-penalties",
    response_model=Envelope[ActualPenaltyResponse],
    status_code=status.HTTP_201_CREATED,
)
def add_actual_penalty(
    purchase_order_id: UUID,
    body: ActualPenaltyRequest,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    actual_penalties: ActualPenaltyRepository = Depends(get_actual_penalty_repository),
) -> Envelope[ActualPenaltyResponse]:
    purchase_orders.require_purchase_order(purchase_order_id)
    created = actual_penalties.add_actual_penalty(purchase_order_id=purchase_order_id, **body.model_dump())
    return success_envelope(ActualPenaltyResponse.model_validate(created), message="Actual penalty recorded.")


@router.get(
    "/purchase-orders/{purchase_order_id}/actual-penalties",
    response_model=Envelope[list[ActualPenaltyResponse]],
)
def list_actual_penalties(
    purchase_order_id: UUID,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    actual_penalties: ActualPenaltyRepository = Depends(get_actual_penalty_repository),
) -> Envelope[list[ActualPenaltyResponse]]:
    purchase_orders.require_purchase_order(purchase_order_id)
    rows = actual_penalties.list_for_purchase_order(purchase_order_id)
    return success_envelope([ActualPenaltyResponse.model_validate(r) for r in rows])
