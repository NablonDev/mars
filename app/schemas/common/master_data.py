"""API schemas for `common`-schema master data: retailers, retailer-owned
locations, SKUs, materials/material-masters, plants, and carriers.

Was `app/schemas/fine_master_data.py`, rewritten against the ERP-normalized
`common` schema (Phase 2 models) -- every row now has a UUID surrogate `id`
in addition to its natural business code, so request bodies carry the
business code (`retailer_code`, `sku_code`, ...) and response bodies add the
surrogate `id` on top.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RetailerRequest(BaseModel):
    retailer_code: str
    retailer_name: str
    priority_tier: str | None = None
    stacking_mode: str = Field(default="SUM", pattern="^(SUM|MAX)$")
    source_system: str | None = None
    extension_min_lead_days: int = 2
    extension_response_sla_hours: int = 48
    extension_penalty_threshold: float = 0.0


class RetailerResponse(RetailerRequest):
    id: UUID


class RetailerLocationRequest(BaseModel):
    location_code: str
    location_name: str | None = None
    location_type: str | None = Field(default=None, pattern="^(PLANT|DC|STORE|OTHER)$")
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    is_active: bool = True


class RetailerLocationResponse(RetailerLocationRequest):
    id: UUID
    retailer_id: UUID


class SkuRequest(BaseModel):
    sku_code: str
    description: str | None = None
    material_id: UUID | None = None


class SkuResponse(SkuRequest):
    id: UUID


class MaterialRequest(BaseModel):
    material_code: str
    description: str | None = None


class MaterialResponse(MaterialRequest):
    id: UUID


class MaterialMasterRequest(BaseModel):
    material_id: UUID
    sap_material_number: str
    plant_id: UUID | None = None
    description: str | None = None
    available_quantity: float | None = None
    uom: str | None = None
    discontinuation_indicator: str | None = None
    effective_out_date: date | None = None
    follow_up_material_id: UUID | None = None
    source_system: str | None = None
    last_synced_at: datetime | None = None


class MaterialMasterResponse(MaterialMasterRequest):
    id: UUID


class PlantRequest(BaseModel):
    plant_code: str
    plant_name: str | None = None
    country_code: str | None = None


class PlantResponse(PlantRequest):
    id: UUID


class CarrierRequest(BaseModel):
    carrier_code: str
    carrier_name: str
    historical_reliability_score: float = Field(default=90.0, ge=0, le=100)


class CarrierResponse(CarrierRequest):
    id: UUID
