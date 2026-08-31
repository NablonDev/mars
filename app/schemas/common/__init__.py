"""API schemas for the `common` domain: master data, purchase orders, and
fulfillment facts, plus the shared `HealthResponse`.

Was the flat `app/schemas/fine_master_data.py`/`orders.py` plus the
standalone `app/schemas/common.py` (this package's `__init__` absorbs that
module -- a package and a same-named top-level module can't coexist).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.common.fulfillment import (
    ActualPenaltyRequest,
    ActualPenaltyResponse,
    DemandExceptionRequest,
    DemandExceptionResponse,
    OrderConfirmationLineCreate,
    OrderConfirmationLineResponse,
    OrderConfirmationRequest,
    OrderConfirmationResponse,
    ShipmentRequest,
    ShipmentResponse,
)
from app.schemas.common.master_data import (
    CarrierRequest,
    CarrierResponse,
    MaterialMasterRequest,
    MaterialMasterResponse,
    MaterialRequest,
    MaterialResponse,
    PlantRequest,
    PlantResponse,
    RetailerLocationRequest,
    RetailerLocationResponse,
    RetailerRequest,
    RetailerResponse,
    SkuRequest,
    SkuResponse,
)
from app.schemas.common.purchase_orders import (
    PurchaseOrderLineCreate,
    PurchaseOrderLineResponse,
    PurchaseOrderRequest,
    PurchaseOrderResponse,
)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unreachable"]


__all__ = [
    "ActualPenaltyRequest",
    "ActualPenaltyResponse",
    "CarrierRequest",
    "CarrierResponse",
    "DemandExceptionRequest",
    "DemandExceptionResponse",
    "HealthResponse",
    "MaterialMasterRequest",
    "MaterialMasterResponse",
    "MaterialRequest",
    "MaterialResponse",
    "OrderConfirmationLineCreate",
    "OrderConfirmationLineResponse",
    "OrderConfirmationRequest",
    "OrderConfirmationResponse",
    "PlantRequest",
    "PlantResponse",
    "PurchaseOrderLineCreate",
    "PurchaseOrderLineResponse",
    "PurchaseOrderRequest",
    "PurchaseOrderResponse",
    "RetailerLocationRequest",
    "RetailerLocationResponse",
    "RetailerRequest",
    "RetailerResponse",
    "ShipmentRequest",
    "ShipmentResponse",
    "SkuRequest",
    "SkuResponse",
]
