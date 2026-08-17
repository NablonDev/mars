"""API schemas for order-related operations."""

from datetime import date, datetime

from pydantic import BaseModel


class OrderRequest(BaseModel):
    order_id: str
    retailer_id: str
    sku_id: str
    ship_from_location_id: str
    order_qty: int
    unit_price: float
    order_date: date
    requested_delivery_date: date
    required_ship_date: date
    carrier_id: str | None = None
    order_status: str = "OPEN"


class ConfirmationRequest(BaseModel):
    confirmation_id: str
    confirmed_qty: int
    confirmation_date: datetime
    cut_reason_code: str | None = None


class ProductionStatusRequest(BaseModel):
    production_id: str
    sku_id: str
    location_id: str
    status: str
    status_date: datetime


class ShipmentEventRequest(BaseModel):
    carrier_id: str | None = None
    expected_ship_date: date | None = None
    actual_ship_date: date | None = None
    appointment_status: str = "SCHEDULED"
    expected_transit_days: int = 2
    recorded_at: datetime


class DemandExceptionRequest(BaseModel):
    exception_id: str
    flagged_date: date


class ActualFineRequest(BaseModel):
    actual_fine_id: str
    violation_type: str
    actual_fine_amount: float
    invoice_or_deduction_date: date
    dispute_status: str = "NONE"
    # retailer_id is deliberately not accepted here -- it's derived from
    # the order (one order belongs to exactly one retailer), not
    # re-supplied by the caller where it could drift out of sync.


class ActualFineResponse(ActualFineRequest):
    order_id: str
    retailer_id: str
