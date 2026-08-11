from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RunProjectionRequest(BaseModel):
    order_id: str | None = None
    all_open: bool = False
    projection_date: date | None = None
    # `Literal`, not `str`: `app.engine.orchestrator.project_order` raises a
    # bare `ValueError` on anything other than SUM/MAX, and validating the
    # value here means bad client input is a schema-level 422 instead of
    # reaching the engine.
    stacking_mode_override: Literal["SUM", "MAX"] | None = None


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
