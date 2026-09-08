"""API schemas for `penalties.penalty_dispute` and its summary trigger/poll contract."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.enums import SummaryStatus


class DisputeOpenRequest(BaseModel):
    """Body for POST /penalties/disputes."""

    actual_penalty_id: UUID
    reason_code: Literal["AMOUNT_INCORRECT", "NOT_LATE", "QTY_CONFIRMED", "RULE_MISAPPLIED", "OTHER"]
    claimed_amount: float
    notes: str | None = None


class DisputeResolveRequest(BaseModel):
    """Body for `POST /penalties/disputes/{dispute_id}/resolve`.

    Omit `override_verdict` to accept the engine's own verdict.
    """

    resolved_by: str
    override_verdict: Literal["NO_PAY", "PAY_PARTIAL", "PAY_FULL"] | None = None
    override_reason: str | None = None

    @model_validator(mode="after")
    def _override_reason_required_with_override_verdict(self) -> DisputeResolveRequest:
        """Enforce that `override_reason` is set if and only if `override_verdict` is set."""
        if self.override_verdict is not None and not self.override_reason:
            raise ValueError("override_reason is required when override_verdict is set")
        if self.override_verdict is None and self.override_reason is not None:
            raise ValueError("override_reason is only valid when override_verdict is set")
        return self


class DisputeAnalysisBreakdownFacts(BaseModel):
    """Raw order/delivery facts the dispute engine used to compute its verdict."""

    order_qty: int
    unit_price: float
    delivered_qty: float | None = None
    shortfall_units: float | None = None
    required_delivery_date: date
    actual_delivery_date: date | None = None
    deadline: date | None = None
    is_late: bool | None = None
    grace_period_days: int


class DisputeAnalysisBreakdown(BaseModel):
    """Explanation of a dispute's verdict: the rule applied, the facts, and the delta."""

    rule_id: str
    rule_code: str
    calc_type: str
    violation_family: str
    as_of_date: date
    facts: DisputeAnalysisBreakdownFacts
    cap_amount: float | None = None
    cap_applied: bool | None = None
    claimed_amount: float
    computed_amount: float
    delta_amount: float


class DisputeResponse(BaseModel):
    """Response shape for a `penalty_dispute` row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dispute_number: str
    actual_penalty_id: UUID
    purchase_order_id: UUID
    rule_id: UUID | None = None
    reason_code: str
    claimed_amount: float
    computed_amount: float | None = None
    delta_amount: float | None = None
    verdict: str | None = None
    dispute_status: str
    analysis_breakdown: DisputeAnalysisBreakdown | None = None
    analyzed_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    override_verdict: str | None = None
    override_reason: str | None = None
    notes: str | None = None


class DisputeSummaryRequest(BaseModel):
    """Body for POST /penalties/disputes/{dispute_id}/summary."""

    force_regenerate: bool = False


class DisputeSummaryResponse(BaseModel):
    """Response shape for a generated dispute narrative summary."""

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


class DisputeSummaryStatusResponse(BaseModel):
    """Poll response for a dispute summary; a null `status` means never requested, not missing."""

    dispute_id: UUID
    as_of_date: date | None
    status: SummaryStatus | None
    summary: DisputeSummaryResponse | None = None
    error_message: str | None = None
