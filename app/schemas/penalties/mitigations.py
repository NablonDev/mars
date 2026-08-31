"""API schemas for `penalties.mitigation_option` requests/responses and the
mitigation-summary trigger/poll contract.

Was `app/schemas/fine_mitigation/mitigations.py` + `summaries.py`."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import SummaryStatus


class MitigationOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_order_id: UUID
    projection_date: date
    action: str | None
    projected_penalty_after: float
    action_cost: float
    net_saving: float
    risk_level: str
    confidence: str
    rationale: str | None


class MitigationOptionsResponse(BaseModel):
    purchase_order_id: UUID
    projection_date: date
    options: list[MitigationOptionResponse]


class PenaltyMitigationSummaryRequest(BaseModel):
    as_of_date: date | None = None
    force_regenerate: bool = False


class PenaltyMitigationSummaryResponse(BaseModel):
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


class PenaltyMitigationSummaryStatusResponse(BaseModel):
    purchase_order_id: UUID
    as_of_date: date
    status: SummaryStatus
    summary: PenaltyMitigationSummaryResponse | None = None
    error_message: str | None = None


class MitigationOptionDetailResponse(MitigationOptionResponse):
    """GET /penalty-mitigations/{mitigation_id}?include=summary -- pure
    read, never schedules generation (approved plan §5)."""

    summary_status: SummaryStatus | None = None
    summary: PenaltyMitigationSummaryResponse | None = None
