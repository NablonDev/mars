"""API endpoints for recording daily operational facts used by projections."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_order_repository
from app.repositories.order import OrderRepository
from app.schemas.orders import (
    ActualFineRequest,
    ActualFineResponse,
    ConfirmationRequest,
    DemandExceptionRequest,
    ProductionStatusRequest,
    ShipmentEventRequest,
)

router = APIRouter(tags=["facts"])


@router.post("/orders/{order_id}/confirmations", status_code=201)
def add_confirmation(
    order_id: str,
    body: ConfirmationRequest,
    orders: OrderRepository = Depends(get_order_repository),
) -> dict:
    orders.require_order(order_id)
    orders.add_confirmation(order_id=order_id, **body.model_dump())
    return {"status": "recorded"}


@router.post("/production-schedules", status_code=201)
def add_production_status(
    body: ProductionStatusRequest,
    orders: OrderRepository = Depends(get_order_repository),
) -> dict:
    orders.add_production_status(**body.model_dump())
    return {"status": "recorded"}


@router.post("/orders/{order_id}/shipments", status_code=201)
def record_shipment_event(
    order_id: str,
    body: ShipmentEventRequest,
    orders: OrderRepository = Depends(get_order_repository),
) -> dict:
    orders.require_order(order_id)
    orders.record_shipment_event(order_id=order_id, **body.model_dump())
    return {"status": "recorded"}


@router.post("/orders/{order_id}/demand-exceptions", status_code=201)
def add_demand_exception(
    order_id: str,
    body: DemandExceptionRequest,
    orders: OrderRepository = Depends(get_order_repository),
) -> dict:
    orders.require_order(order_id)
    orders.add_demand_exception(order_id=order_id, **body.model_dump())
    return {"status": "recorded"}


@router.post(
    "/orders/{order_id}/actual-fines",
    response_model=ActualFineResponse,
    status_code=201,
)
def add_actual_fine(
    order_id: str,
    body: ActualFineRequest,
    orders: OrderRepository = Depends(get_order_repository),
) -> dict:
    order = orders.require_order(order_id)
    orders.add_actual_fine(
        order_id=order_id,
        retailer_id=order["retailer_id"],
        **body.model_dump(),
    )
    return {
        **body.model_dump(),
        "order_id": order_id,
        "retailer_id": order["retailer_id"],
    }


@router.get(
    "/orders/{order_id}/actual-fines",
    response_model=list[ActualFineResponse],
)
def list_actual_fines(
    order_id: str,
    orders: OrderRepository = Depends(get_order_repository),
) -> list[dict]:
    orders.require_order(order_id)
    return orders.list_actual_fines(order_id)
