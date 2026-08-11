"""
The structured-output contract every projection explanation must
conform to, shared across shortage-only / delay-only / stacked /
locked-in cases. This is a distinct type from `app.schemas.explanations`
(the API response schema) for the same reason `app/schemas/projections.py`
doesn't reuse `app.engine.ProjectionResult` directly -- the API contract
is an independently versionable thing even where fields currently match.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ViolationExplanation(BaseModel):
    violation_type: str
    rule_id: str
    probability: float
    fine_if_realized: float
    expected_fine: float
    explanation: str


class DayChangeEntry(BaseModel):
    projection_date: date
    total_expected_fine: float
    change_summary: str


class Caveat(BaseModel):
    """`kind` is a short machine-readable tag (e.g. "STACKING_AMBIGUITY",
    "SHARED_PLANT") so a client UI can style/group caveats consistently;
    `message` is the human-readable explanation."""

    kind: str
    message: str


class ProjectionExplanationOutput(BaseModel):
    order_id: str
    as_of_date: date
    headline_summary: str
    current_total_expected_fine: float
    stacking_mode: str
    violations: list[ViolationExplanation]
    day_by_day_narrative: list[DayChangeEntry]
    key_sensitivity_factors: list[str]
    caveats: list[Caveat] = Field(default_factory=list)
