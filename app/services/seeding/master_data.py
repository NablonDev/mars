"""Master/reference data (retailers, materials/SKUs, plants, carriers)
shared by both penalty-projection and penalty-mitigation seeding -- not
exclusive to either sub-domain, so it stays out of their seeding modules.

Was `app/services/seeding/master_data.py` against the old flat
`sku`/`location` model. Rewritten against the ERP-normalized `common`
schema (Phase 3 -- services move/folder-split):

- The old flat `sku` row (`sku_id`, `sku_code` -- which was actually a
  material-like code, e.g. "MAT-100234") is now two rows: a `material`
  (plant-agnostic identity, `material_code` = the old `sku_code`) and a
  `sku` (`sku_code` = the old `sku_id`, `material_id` FK to the new
  `material` row). `material_master` (the per-plant stock/logistics
  extension) is NOT seeded here -- none of the four worked-example
  scenarios' projection/mitigation math reads it; only PO-validation's
  quantity check does, and that domain seeds its own fixtures separately
  (out of scope for this seed set).
- The old `location` row (one flat table for both a manufacturing plant and
  a distribution center) is now a `plant` row for each -- `warehouse` is a
  distinct concept in the new schema (a `delivery.ship_from_warehouse_id`
  target) that neither worked example's seed data or engine inputs need.
"""

from __future__ import annotations

from typing import TypedDict

from app.repositories.common.master_data import MasterDataRepository


class _RetailerSeed(TypedDict):
    retailer_code: str
    retailer_name: str
    priority_tier: str
    stacking_mode: str
    extension_min_lead_days: int
    extension_response_sla_hours: int
    extension_penalty_threshold: float


_RETAILERS: list[_RetailerSeed] = [
    {
        "retailer_code": "RET-WMT",
        "retailer_name": "Walmart",
        "priority_tier": "TIER_1",
        "stacking_mode": "SUM",
        "extension_min_lead_days": 2,
        "extension_response_sla_hours": 48,
        "extension_penalty_threshold": 200.0,
    },
    {
        "retailer_code": "RET-AMZ",
        "retailer_name": "Amazon",
        "priority_tier": "TIER_1",
        "stacking_mode": "SUM",
        # Amazon's shorter SLA is deliberate -- it's what makes the
        # AMZ-780112 timeout scenario in the seeding day-loop actually
        # expire within that order's own scenario window.
        "extension_min_lead_days": 2,
        "extension_response_sla_hours": 24,
        "extension_penalty_threshold": 100.0,
    },
]

# (material_code, sku_code, description) -- material_code was the old
# scenario data's "sku_code" (e.g. "MAT-100234"); sku_code was the old
# "sku_id" (e.g. "SKU-PED30").
_MATERIALS_AND_SKUS = [
    ("MAT-100234", "SKU-PED30", "Pedigree Adult Dry Dog Food 30lb"),
    ("MAT-100511", "SKU-CES12", "Cesar Adult Wet Dog Food Variety Pack 12ct"),
    ("MAT-100587", "SKU-WHI20", "Whiskas Adult Dry Cat Food 20lb"),
]

# (plant_code, plant_name) -- was `_LOCATIONS`; both the manufacturing
# plant and the distribution center become `plant` rows (see module
# docstring).
_PLANTS = [
    ("LOC-COL", "Mars Petcare Plant - Columbia MO"),
    ("LOC-ATL", "Mars DC - Atlanta GA"),
]


class _CarrierSeed(TypedDict):
    carrier_code: str
    carrier_name: str
    historical_reliability_score: float


_CARRIERS: list[_CarrierSeed] = [
    {
        "carrier_code": "CAR-SWIFT",
        "carrier_name": "Swift Transportation",
        "historical_reliability_score": 92.0,
    },
    {"carrier_code": "CAR-JBHUNT", "carrier_name": "JB Hunt", "historical_reliability_score": 78.0},
]


def seed(master_data: MasterDataRepository) -> dict[str, int]:
    """Idempotent: safe to call repeatedly. Skips anything that already
    exists rather than erroring on a duplicate key."""
    counts = {"retailers": 0, "materials": 0, "skus": 0, "plants": 0, "carriers": 0}

    existing_retailers = {r["retailer_code"] for r in master_data.list_retailers()}
    for r in _RETAILERS:
        if r["retailer_code"] not in existing_retailers:
            master_data.add_retailer(
                r["retailer_code"],
                r["retailer_name"],
                r["priority_tier"],
                r["stacking_mode"],
                None,
                r["extension_min_lead_days"],
                r["extension_response_sla_hours"],
                r["extension_penalty_threshold"],
            )
            counts["retailers"] += 1

    existing_skus = {s["sku_code"] for s in master_data.list_skus()}
    for material_code, sku_code, description in _MATERIALS_AND_SKUS:
        material = master_data.get_material_by_code(material_code)
        if material is None:
            material = master_data.add_material(material_code, description)
            counts["materials"] += 1
        if sku_code not in existing_skus:
            master_data.add_sku(sku_code, description, material["id"])
            counts["skus"] += 1

    existing_plants = {p["plant_code"] for p in master_data.list_plants()}
    for plant_code, plant_name in _PLANTS:
        if plant_code not in existing_plants:
            master_data.add_plant(plant_code, plant_name)
            counts["plants"] += 1

    existing_carriers = {c["carrier_code"] for c in master_data.list_carriers()}
    for c in _CARRIERS:
        if c["carrier_code"] not in existing_carriers:
            master_data.add_carrier(c["carrier_code"], c["carrier_name"], c["historical_reliability_score"])
            counts["carriers"] += 1

    return counts
