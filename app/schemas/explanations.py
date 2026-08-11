from datetime import date

from pydantic import BaseModel, ConfigDict


class ExplainProjectionRequest(BaseModel):
    as_of_date: date | None = None
    force_regenerate: bool = False


class ViolationExplanationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    violation_type: str
    rule_id: str
    probability: float
    fine_if_realized: float
    expected_fine: float
    explanation: str


class DayChangeEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    projection_date: date
    total_expected_fine: float
    change_summary: str


class CaveatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    message: str


class ExplanationResponse(BaseModel):
    """Kept as a separate schema-layer type from `app.agents.explanation_schema.
    ProjectionExplanationOutput`, not a direct re-export -- same reason
    `app/schemas/projections.py` doesn't reuse `app.engine.ProjectionResult`
    directly: the API contract is an independently versionable thing even
    where fields currently match."""

    model_config = ConfigDict(from_attributes=True)

    order_id: str
    as_of_date: date
    headline_summary: str
    current_total_expected_fine: float
    stacking_mode: str
    violations: list[ViolationExplanationResponse]
    day_by_day_narrative: list[DayChangeEntryResponse]
    key_sensitivity_factors: list[str]
    caveats: list[CaveatResponse]
