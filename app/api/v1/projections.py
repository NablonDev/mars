from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import (
    get_order_repository,
    get_projection_repository,
    get_projection_service,
)
from app.repositories.order_repository import OrderRepository
from app.repositories.projection_repository import ProjectionRepository
from app.schemas.projections import (
    ExposureResponse,
    ProjectionHistoryRow,
    ProjectionResultResponse,
    RunProjectionRequest,
)
from app.services.projection_service import ProjectionService

router = APIRouter(tags=["projections"])


@router.post("/projections/run", response_model=list[ProjectionResultResponse])
def run_projection(
    body: RunProjectionRequest, projection_service: ProjectionService = Depends(get_projection_service)
) -> list:
    if not body.order_id and not body.all_open:
        raise HTTPException(status_code=422, detail="Pass either order_id or all_open=true")

    # No try/except: `OrderNotFoundError` (404) and `NoActiveRulesError`
    # (422) are both `AppError`s, mapped once in
    # app/core/exceptions.py::register_exception_handlers.
    if body.all_open:
        results = projection_service.run_for_all_open(body.projection_date, body.stacking_mode_override)
    else:
        results = [
            projection_service.run_for_order(body.order_id, body.projection_date, body.stacking_mode_override)
        ]

    return [ProjectionResultResponse.model_validate(r) for r in results]


@router.get("/orders/{order_id}/projections", response_model=list[ProjectionHistoryRow])
def get_projection_history(
    order_id: str,
    orders: OrderRepository = Depends(get_order_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
) -> list[dict]:
    if orders.get_order(order_id) is None:
        raise HTTPException(status_code=404, detail=f"No order found with order_id={order_id!r}")
    return projections.get_history(order_id)


@router.get("/orders/{order_id}/exposure", response_model=ExposureResponse)
def get_exposure(
    order_id: str,
    orders: OrderRepository = Depends(get_order_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
) -> dict:
    if orders.get_order(order_id) is None:
        raise HTTPException(status_code=404, detail=f"No order found with order_id={order_id!r}")
    latest = projections.get_latest(order_id)
    if latest is None:
        raise HTTPException(status_code=404, detail=f"No projections exist yet for order_id={order_id!r}")
    return latest
