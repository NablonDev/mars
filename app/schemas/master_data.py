"""API schemas for master data requests and responses."""

from pydantic import BaseModel, Field


class RetailerRequest(BaseModel):
    retailer_id: str
    retailer_name: str
    priority_tier: str | None = None
    stacking_mode: str = Field(default="SUM", pattern="^(SUM|MAX)$")


class SkuRequest(BaseModel):
    sku_id: str
    sku_code: str
    description: str | None = None


class LocationRequest(BaseModel):
    location_id: str
    location_name: str | None = None
    location_type: str | None = Field(default=None, pattern="^(PLANT|DC)$")


class CarrierRequest(BaseModel):
    carrier_id: str
    carrier_name: str
    historical_reliability_score: float = Field(default=90.0, ge=0, le=100)
