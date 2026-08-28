"""API schemas for mitigation-options requests and responses."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.schemas.fine_mitigation.summaries import MitigationSummaryStatusResponse


class OrderMitigationRequest(BaseModel):
    """Body for POST /orders/{order_id}/mitigation-options -- mirrors
    OrderProjectionRequest; order_id comes from the path."""

    projection_date: date | None = None


class MitigationOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action: str
    projected_fine_after: float
    action_cost: float
    net_saving: float
    risk_level: str
    confidence: str
    rationale: str


class MitigationOptionsResponse(BaseModel):
    order_id: str
    projection_date: date
    options: list[MitigationOptionResponse]


class MitigationRunRequest(BaseModel):
    """Body for POST /orders/{order_id}/mitigation-options/runs -- mitigation
    options, then mitigation summary, for the same projection_date."""

    projection_date: date | None = None
    force_regenerate_summary: bool = False


class MitigationRunResponse(BaseModel):
    """Response for POST /orders/{order_id}/mitigation-options/runs."""

    mitigation_options: MitigationOptionsResponse
    summary: MitigationSummaryStatusResponse
