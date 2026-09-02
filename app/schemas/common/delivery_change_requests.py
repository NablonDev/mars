"""API schemas for `po_delivery_change_request` (common/unqualified schema).

Was `app/schemas/fine_projection/po_delivery_change_requests.py`. Flattened
to `/delivery-change-requests` (was nested under
`/purchase-orders/{purchase_order_id}/delivery-change-requests`) -- see
`app/api/v1/common/delivery_change_requests.py`'s module docstring for
the same flattening rationale already applied to
`app.api.v1.penalties.projections`/`mitigations`."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator


class DeliveryChangeRequestCreate(BaseModel):
    """Body for POST /delivery-change-requests."""

    purchase_order_id: UUID
    reason_code: Literal["SHORTAGE", "DELAY", "OTHER"]
    proposed_delivery_date: date
    notes: str | None = None


class DeliveryChangeResponseRequest(BaseModel):
    """Body for POST .../delivery-change-requests/{delivery_change_request_id}/response --
    mock/manual retailer-response entry.

    Only self-contained validation lives here (presence of
    countered_delivery_date iff decision == COUNTERED); the "must fall
    strictly between baseline_delivery_date and proposed_delivery_date" rule
    needs the stored request row and is enforced in
    PoDeliveryChangeRequestService.record_response.
    """

    decision: Literal["ACCEPTED", "COUNTERED", "REJECTED"]
    countered_delivery_date: date | None = None

    @model_validator(mode="after")
    def _countered_date_matches_decision(self) -> DeliveryChangeResponseRequest:
        if self.decision == "COUNTERED" and self.countered_delivery_date is None:
            raise ValueError("countered_delivery_date is required when decision=COUNTERED")
        if self.decision != "COUNTERED" and self.countered_delivery_date is not None:
            raise ValueError("countered_delivery_date is only valid when decision=COUNTERED")
        return self


class DeliveryChangeRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_order_id: UUID
    reason_code: str
    requested_at: datetime
    baseline_delivery_date: date
    proposed_delivery_date: date
    expires_at: datetime
    status: str
    retailer_response_date: date | None = None
    countered_delivery_date: date | None = None
    resolved_at: datetime | None = None
    response_payload: dict | None = None
    notes: str | None = None
