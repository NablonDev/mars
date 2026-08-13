"""API schemas for fine-rule requests and responses."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FineTierSchema(BaseModel):
    band_min: float = Field(ge=0.0, le=1.0)
    band_max: float = Field(ge=0.0, le=1.01)  # 1.01 lets a top band close "30%+" as (0.30, 1.01)
    rate: float


class FineRuleRequest(BaseModel):
    rule_id: str
    retailer_id: str
    violation_type: str
    calc_type: Literal["PER_UNIT", "PERCENT_OF_PO", "FLAT_FEE", "TIERED"]
    rate: float = 0.0
    threshold_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="FRACTION, e.g. 0.02 for 2% -- never a whole-number percent.",
    )
    cap_amount: float | None = None
    grace_period_days: int = 0
    effective_start_date: date = date(2026, 1, 1)
    effective_end_date: date | None = None
    source_doc_reference: str | None = None
    tiers: list[FineTierSchema] | None = None

    @model_validator(mode="after")
    def _tiered_requires_tiers(self) -> "FineRuleRequest":
        if self.calc_type == "TIERED" and not self.tiers:
            raise ValueError("calc_type=TIERED requires at least one tier band")
        return self


class FineRuleResponse(BaseModel):
    rule_id: str
    retailer_id: str
    violation_type: str
    calc_type: str
    rate: float
    threshold_pct: float
    cap_amount: float | None
    is_active: bool
    grace_period_days: int
    effective_start_date: date
    effective_end_date: date | None
    source_doc_reference: str | None
