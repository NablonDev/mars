"""
The operational facts an ETL job (or, today, a demo script) writes daily:
SAP cut-order confirmations, production status, shipment/appointment
state, demand exceptions, and post-delivery actual fines. Each write is
what `ProjectionService` reads back through `OrderRepository.build_snapshot`.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_order_repository
from app.repositories.order_repository import OrderRepository
from app.schemas.orders import (
    ActualFineRequest,
    ActualFineResponse,
    ConfirmationRequest,
    DemandExceptionRequest,
    ProductionStatusRequest,
    ShipmentEventRequest,
)

router = APIRouter(tags=["facts"])


def _require_order(orders: OrderRepository, order_id: str) -> dict:
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"No order found with order_id={order_id!r}")
    return order


@router.post("/orders/{order_id}/confirmations", status_code=201)
def add_confirmation(
    order_id: str, body: ConfirmationRequest, orders: OrderRepository = Depends(get_order_repository)
) -> dict:
    _require_order(orders, order_id)
    orders.add_confirmation(order_id=order_id, **body.model_dump())
    return {"status": "recorded"}


@router.post("/production-schedule", status_code=201)
def add_production_status(
    body: ProductionStatusRequest, orders: OrderRepository = Depends(get_order_repository)
) -> dict:
    orders.add_production_status(**body.model_dump())
    return {"status": "recorded"}


@router.put("/orders/{order_id}/shipment", status_code=201)
def record_shipment_event(
    order_id: str, body: ShipmentEventRequest, orders: OrderRepository = Depends(get_order_repository)
) -> dict:
    _require_order(orders, order_id)
    orders.record_shipment_event(order_id=order_id, **body.model_dump())
    return {"status": "recorded"}


@router.post("/orders/{order_id}/demand-exceptions", status_code=201)
def add_demand_exception(
    order_id: str, body: DemandExceptionRequest, orders: OrderRepository = Depends(get_order_repository)
) -> dict:
    _require_order(orders, order_id)
    orders.add_demand_exception(order_id=order_id, **body.model_dump())
    return {"status": "recorded"}


@router.post("/orders/{order_id}/actual-fines", response_model=ActualFineResponse, status_code=201)
def add_actual_fine(
    order_id: str, body: ActualFineRequest, orders: OrderRepository = Depends(get_order_repository)
) -> dict:
    order = _require_order(orders, order_id)
    orders.add_actual_fine(order_id=order_id, retailer_id=order["retailer_id"], **body.model_dump())
    return {**body.model_dump(), "order_id": order_id, "retailer_id": order["retailer_id"]}


@router.get("/orders/{order_id}/actual-fines", response_model=list[ActualFineResponse])
def list_actual_fines(order_id: str, orders: OrderRepository = Depends(get_order_repository)) -> list[dict]:
    _require_order(orders, order_id)
    return orders.list_actual_fines(order_id)
