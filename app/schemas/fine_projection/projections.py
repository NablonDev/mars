"""API schemas for projection requests and responses."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.fine_projection.summaries import ProjectionSummaryStatusResponse


class RunProjectionRequest(BaseModel):
    order_id: str | None = None
    all_open: bool = False
    projection_date: date | None = None
    stacking_mode_override: Literal["SUM", "MAX"] | None = None


class OrderProjectionRequest(BaseModel):
    """Body for POST /orders/{order_id}/projections -- single-order run,
    order_id comes from the path rather than the body."""

    projection_date: date | None = None
    stacking_mode_override: Literal["SUM", "MAX"] | None = None


class OrderRunRequest(BaseModel):
    """Body for POST /orders/{order_id}/projections/runs -- projection, then summary."""

    projection_date: date | None = None
    stacking_mode_override: Literal["SUM", "MAX"] | None = None
    force_regenerate_summary: bool = False


class ViolationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    violation_type: str
    rule_id: str
    probability: float
    fine_if_realized: float
    expected_fine: float


class ProjectionResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    projection_date: date
    days_to_delivery: int
    shortage_probability: float
    delay_probability: float
    violations: list[ViolationResponse]
    total_expected_fine: float
    stacking_mode: str


class ProjectionHistoryRow(BaseModel):
    rule_id: str
    projection_date: date
    violation_type: str
    failure_probability: float
    projected_fine_amount: float
    days_to_delivery: int
    projection_status: str


class ExposureResponse(BaseModel):
    order_id: str
    projection_date: date
    total_expected_fine: float
    violations: list[ProjectionHistoryRow]


class OrderRunResponse(BaseModel):
    """Response for POST /orders/{order_id}/projections/runs."""

    projection: ProjectionResultResponse
    summary: ProjectionSummaryStatusResponse
