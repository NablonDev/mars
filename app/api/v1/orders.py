"""API endpoints for creating and retrieving orders."""

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_order_repository
from app.repositories.order import OrderRepository
from app.schemas.orders import OrderRequest

router = APIRouter(tags=["orders"])


@router.post("/orders", response_model=OrderRequest, status_code=201)
def create_order(
    body: OrderRequest,
    orders: OrderRepository = Depends(get_order_repository),
) -> dict:
    orders.create_order(**body.model_dump())
    return orders.get_order(body.order_id)


@router.get("/orders", response_model=list[OrderRequest])
def list_orders(
    order_status: str | None = Query(default=None),
    orders: OrderRepository = Depends(get_order_repository),
) -> list[dict]:
    return orders.list_orders(order_status)


@router.get("/orders/{order_id}", response_model=OrderRequest)
def get_order(
    order_id: str,
    orders: OrderRepository = Depends(get_order_repository),
) -> dict:
    return orders.require_order(order_id)
