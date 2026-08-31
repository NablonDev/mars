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
    shortage_penalty: float
    delay_penalty: float
    total_expected_penalty: float
    shortage_probability: float
    delay_probability: float


class ScenarioSummary(BaseModel):
    purchase_order_id: str
    days: list[ScenarioDayResult]
    negotiation: dict | None = None


class SimulateDailyRunResponse(BaseModel):
    scenarios: list[ScenarioSummary]
