"""API schemas for `common`-schema fulfillment facts: order confirmations,
shipments, demand exceptions, and actual (post-delivery) penalties.

Was the flat, single-line `ConfirmationRequest`/`ShipmentEventRequest`/
`DemandExceptionRequest`/`ActualFineRequest` in `app/schemas/orders.py`.
Every fact is now keyed against a specific `purchase_order_line_id` (a PO can
have more than one line) except shipments, which attach to a `delivery`
(itself keyed by `purchase_order_id`) -- see
`app/repositories/common/fulfillment.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OrderConfirmationLineCreate(BaseModel):
    purchase_order_line_id: UUID
    confirmed_quantity: float
    confirmed_delivery_date: date | None = None
    cut_reason_code: str | None = None


class OrderConfirmationLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_confirmation_id: UUID
    purchase_order_line_id: UUID
    confirmed_quantity: float
    confirmed_delivery_date: date | None = None
    cut_reason_code: str | None = None


class OrderConfirmationRequest(BaseModel):
    confirmation_number: str
    confirmation_date: datetime
    status: str | None = None
    # Optional: when omitted, applies to the PO's first line -- the same
    # single-primary-line convenience `ProjectionService.build_snapshot`
    # and the seed data use.
    lines: list[OrderConfirmationLineCreate] = []


class OrderConfirmationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    confirmation_number: str
    purchase_order_id: UUID
    confirmation_date: datetime
    status: str | None = None
    lines: list[OrderConfirmationLineResponse] = []


class ShipmentRequest(BaseModel):
    shipment_number: str
    delivery_number: str | None = None
    carrier_id: UUID | None = None
    expected_ship_date: date | None = None
    actual_ship_date: date | None = None
    expected_delivery_date: date | None = None
    actual_delivery_date: date | None = None
    appointment_status: Literal["SCHEDULED", "RESCHEDULED", "MISSED", "COMPLETED"] = "SCHEDULED"
    expected_transit_days: int = 2
    shipment_status: str = "SCHEDULED"
    recorded_at: datetime


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    shipment_number: str
    delivery_id: UUID
    carrier_id: UUID | None = None
    expected_ship_date: date | None = None
    actual_ship_date: date | None = None
    expected_delivery_date: date | None = None
    actual_delivery_date: date | None = None
    expected_transit_days: int | None = None
    appointment_status: str | None = None
    shipment_status: str
    recorded_at: datetime


class DemandExceptionRequest(BaseModel):
    exception_id: str
    purchase_order_line_id: UUID | None = None
    flagged_date: date


class DemandExceptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exception_id: str
    purchase_order_line_id: UUID
    flagged_date: date
    resolved: bool


class ActualPenaltyRequest(BaseModel):
    actual_penalty_number: str
    violation_type: str
    actual_penalty_amount: float
    invoice_or_deduction_date: date
    dispute_status: str = "NONE"


class ActualPenaltyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actual_penalty_number: str
    purchase_order_id: UUID
    violation_type: str
    actual_penalty_amount: float
    invoice_or_deduction_date: date
    dispute_status: str
