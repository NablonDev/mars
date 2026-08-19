"""API schemas for admin requests and responses."""

from datetime import date

from pydantic import BaseModel


class SeedMasterDataResponse(BaseModel):
    retailers: int
    skus: int
    locations: int
    carriers: int
    rules: int
    orders: int


class ScenarioDayResult(BaseModel):
    projection_date: date
    note: str
    shortage_fine: float
    delay_fine: float
    total_expected_fine: float
    shortage_probability: float
    delay_probability: float


class ScenarioSummary(BaseModel):
    order_id: str
    days: list[ScenarioDayResult]


class SimulateDailyRunResponse(BaseModel):
    scenarios: list[ScenarioSummary]
