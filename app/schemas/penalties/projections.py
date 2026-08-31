"""API schemas for `penalties.penalty_projection` requests/responses and the
projection-summary trigger/poll contract.

Was `app/schemas/fine_projection/projections.py` + `summaries.py`. Field
names drop the stale `fine`/`order` vocabulary (`projected_fine_amount` ->
`projected_penalty_amount`, matching `PenaltyProjectionRepository`'s own
dict shape; `order_id` -> `purchase_order_id`); the pure-calc engine's
dataclasses themselves are NOT renamed this phase (see
`app.services.penalties.projection.types`'s module docstring) -- the route
layer maps field-for-field where the two vocabularies diverge.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import SummaryStatus


class PenaltyProjectionRunRequest(BaseModel):
    """Body for POST /purchase-orders/{purchase_order_id}/penalty-projections."""

    projection_date: date | None = None
    stacking_mode_override: Literal["SUM", "MAX"] | None = None


class ViolationResponse(BaseModel):
    violation_type: str
    rule_id: str
    probability: float
    penalty_if_realized: float
    expected_penalty: float


class PenaltyProjectionResultResponse(BaseModel):
    """One projection run's engine output -- POST response shape."""

    purchase_order_id: UUID
    projection_date: date
    days_to_delivery: int
    shortage_probability: float
    delay_probability: float
    violations: list[ViolationResponse]
    total_expected_penalty: float
    stacking_mode: str


class PenaltyProjectionHistoryRow(BaseModel):
    """One persisted `penalty_projection` row -- GET history / cross-PO
    open-list / single-projection-read shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_order_id: UUID
    rule_id: UUID
    projection_date: date
    violation_type: str
    failure_probability: float
    projected_penalty_amount: float
    days_to_delivery: int
    projection_status: str


class PenaltyExposureResponse(BaseModel):
    purchase_order_id: UUID
    projection_date: date
    total_expected_penalty: float
    violations: list[PenaltyProjectionHistoryRow]


class PenaltyProjectionSummaryRequest(BaseModel):
    """Body for POST /purchase-orders/{purchase_order_id}/penalty-projections/summary."""

    as_of_date: date | None = None
    force_regenerate: bool = False


class PenaltyProjectionSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    as_of_date: date
    prompt_version: str
    model_name: str
    summary: str
    is_reused: bool = False
    generated_for_date: date | None = None
    unchanged_since: date | None = None
    unchanged_for_days: int | None = None


class PenaltyProjectionSummaryStatusResponse(BaseModel):
    purchase_order_id: UUID
    as_of_date: date
    status: SummaryStatus
    summary: PenaltyProjectionSummaryResponse | None = None
    error_message: str | None = None


class PenaltyProjectionDetailResponse(PenaltyProjectionHistoryRow):
    """`?include=summary` shape -- pure read, never schedules generation
    (approved plan §5). Used both for
    `GET /penalty-projections/{projection_id}?include=summary` and the
    nested `GET .../penalty-projections?include=summary` history view.
    `summary_status` is always populated once `include=summary` is
    requested (`READY`/`PENDING`/`FAILED`, from cached state only);
    `summary` itself is populated only when `summary_status == READY`.
    """

    summary_status: SummaryStatus | None = None
    summary: PenaltyProjectionSummaryResponse | None = None
