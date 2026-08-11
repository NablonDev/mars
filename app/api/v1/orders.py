from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_order_repository
from app.repositories.order_repository import OrderRepository
from app.schemas.orders import OrderRequest, OrderResponse

router = APIRouter(tags=["orders"])


@router.post("/orders", response_model=OrderResponse, status_code=201)
def create_order(body: OrderRequest, orders: OrderRepository = Depends(get_order_repository)) -> dict:
    if orders.get_order(body.order_id) is not None:
        raise HTTPException(status_code=409, detail=f"Order {body.order_id!r} already exists")
    orders.create_order(**body.model_dump())
    return orders.get_order(body.order_id)


@router.get("/orders", response_model=list[OrderResponse])
def list_orders(
    order_status: str | None = Query(default=None), orders: OrderRepository = Depends(get_order_repository)
) -> list[dict]:
    return orders.list_orders(order_status)


@router.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, orders: OrderRepository = Depends(get_order_repository)) -> dict:
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"No order found with order_id={order_id!r}")
    return order
