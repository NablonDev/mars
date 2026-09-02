"""API schemas for the demo seed-data / daily-scenario-replay admin
endpoints. Was `app/schemas/admin.py`, rewritten against
`app.services.seeding.service.PenaltySeedingService`'s actual return shape
(Phase 3 rewrite) -- field names below match its `seed_master_data()`/
`simulate_daily_run()` dict output 1:1, not the pre-restructure schema."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class SeedDataResponse(BaseModel):
    retailers: int
    materials: int
    skus: int
    plants: int
    carriers: int
    rules: int
    orders: int
    mitigation_inputs: int


class ScenarioDayResult(BaseModel):
    projection_date: date
    note: str
    # Raw, if-realized dollar amounts (not probability-weighted) -- the
    # primary figure a narrative should pair with the probability below.
    # See docs/API.md's penalty_amount/expected_penalty_amount note: the two
    # must always be shown as separate numbers, never collapsed into one.
    shortage_penalty_amount: float
    delay_penalty_amount: float
    # Blended (probability x raw) risk-adjusted figures -- a secondary,
    # clearly-labeled supporting number, never shown as the only one.
    shortage_expected_penalty_amount: float
    delay_expected_penalty_amount: float
    total_expected_penalty_amount: float
    shortage_probability: float
    delay_probability: float


class ScenarioSummary(BaseModel):
    purchase_order_id: str
    days: list[ScenarioDayResult]
    negotiation: dict | None = None


class SimulateDailyRunResponse(BaseModel):
    scenarios: list[ScenarioSummary]
