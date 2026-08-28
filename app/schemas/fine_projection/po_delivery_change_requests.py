"""API schemas for PO delivery-date change requests."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class PoDeliveryChangeRequestCreate(BaseModel):
    """Body for POST /orders/{order_id}/po-delivery-change-requests -- order_id comes from the path."""

    reason_code: Literal["SHORTAGE", "DELAY", "OTHER"]
    proposed_delivery_date: date
    notes: str | None = None


class PoDeliveryChangeResponseRequest(BaseModel):
    """Body for POST /po-delivery-change-requests/{request_id}/response -- mock/manual retailer-response entry.

    Only self-contained validation lives here (presence of countered_delivery_date
    iff decision == COUNTERED); the "must fall strictly between
    baseline_delivery_date and proposed_delivery_date" rule needs the
    stored request row and is enforced in PoDeliveryChangeRequestService.record_response.
    """

    decision: Literal["ACCEPTED", "COUNTERED", "REJECTED"]
    countered_delivery_date: date | None = None

    @model_validator(mode="after")
    def _countered_date_matches_decision(self) -> PoDeliveryChangeResponseRequest:
        if self.decision == "COUNTERED" and self.countered_delivery_date is None:
            raise ValueError("countered_delivery_date is required when decision=COUNTERED")
        if self.decision != "COUNTERED" and self.countered_delivery_date is not None:
            raise ValueError("countered_delivery_date is only valid when decision=COUNTERED")
        return self


class PoDeliveryChangeRequestResponse(BaseModel):
    request_id: str
    order_id: str
    reason_code: str
    requested_at: datetime
    baseline_delivery_date: date
    proposed_delivery_date: date
    expires_at: datetime
    status: str
    retailer_response_date: date | None = None
    countered_delivery_date: date | None = None
    resolved_at: datetime | None = None
    notes: str | None = None
