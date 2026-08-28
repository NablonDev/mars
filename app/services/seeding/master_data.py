"""Master/reference data (retailers, SKUs, locations, carriers) shared by
both fine_projection and fine_mitigation seeding -- not exclusive to
either sub-domain, so it stays out of their seeding modules."""

from __future__ import annotations

from typing import TypedDict

from app.repositories.fine_master_data import MasterDataRepository


class _RetailerSeed(TypedDict):
    retailer_id: str
    retailer_name: str
    priority_tier: str
    stacking_mode: str
    extension_min_lead_days: int
    extension_response_sla_hours: int
    extension_fine_threshold: float


_RETAILERS: list[_RetailerSeed] = [
    {
        "retailer_id": "RET-WMT",
        "retailer_name": "Walmart",
        "priority_tier": "TIER_1",
        "stacking_mode": "SUM",
        "extension_min_lead_days": 2,
        "extension_response_sla_hours": 48,
        "extension_fine_threshold": 200.0,
    },
    {
        "retailer_id": "RET-AMZ",
        "retailer_name": "Amazon",
        "priority_tier": "TIER_1",
        "stacking_mode": "SUM",
        # Amazon's shorter SLA is deliberate -- it's what makes the
        # AMZ-780112 timeout scenario in the seeding day-loop actually
        # expire within that order's own scenario window.
        "extension_min_lead_days": 2,
        "extension_response_sla_hours": 24,
        "extension_fine_threshold": 100.0,
    },
]
_SKUS = [
    {"sku_id": "SKU-PED30", "sku_code": "MAT-100234", "description": "Pedigree Adult Dry Dog Food 30lb"},
    {"sku_id": "SKU-CES12", "sku_code": "MAT-100511", "description": "Cesar Adult Wet Dog Food Variety Pack 12ct"},
    {"sku_id": "SKU-WHI20", "sku_code": "MAT-100587", "description": "Whiskas Adult Dry Cat Food 20lb"},
]
_LOCATIONS = [
    {"location_id": "LOC-COL", "location_name": "Mars Petcare Plant - Columbia MO", "location_type": "PLANT"},
    {"location_id": "LOC-ATL", "location_name": "Mars DC - Atlanta GA", "location_type": "DC"},
]


class _CarrierSeed(TypedDict):
    carrier_id: str
    carrier_name: str
    historical_reliability_score: float


_CARRIERS: list[_CarrierSeed] = [
    {"carrier_id": "CAR-SWIFT", "carrier_name": "Swift Transportation", "historical_reliability_score": 92.0},
    {"carrier_id": "CAR-JBHUNT", "carrier_name": "JB Hunt", "historical_reliability_score": 78.0},
]


def seed(master_data: MasterDataRepository) -> dict[str, int]:
    """Idempotent: safe to call repeatedly. Skips anything that already
    exists rather than erroring on a duplicate key."""
    counts = {"retailers": 0, "skus": 0, "locations": 0, "carriers": 0}

    existing_retailers = {r["retailer_id"] for r in master_data.list_retailers()}
    for r in _RETAILERS:
        if r["retailer_id"] not in existing_retailers:
            master_data.add_retailer(
                r["retailer_id"],
                r["retailer_name"],
                r["priority_tier"],
                r["stacking_mode"],
                r["extension_min_lead_days"],
                r["extension_response_sla_hours"],
                r["extension_fine_threshold"],
            )
            counts["retailers"] += 1

    existing_skus = {s["sku_id"] for s in master_data.list_skus()}
    for s in _SKUS:
        if s["sku_id"] not in existing_skus:
            master_data.add_sku(s["sku_id"], s["sku_code"], s["description"])
            counts["skus"] += 1

    existing_locations = {loc["location_id"] for loc in master_data.list_locations()}
    for loc in _LOCATIONS:
        if loc["location_id"] not in existing_locations:
            master_data.add_location(loc["location_id"], loc["location_name"], loc["location_type"])
            counts["locations"] += 1

    existing_carriers = {c["carrier_id"] for c in master_data.list_carriers()}
    for c in _CARRIERS:
        if c["carrier_id"] not in existing_carriers:
            master_data.add_carrier(c["carrier_id"], c["carrier_name"], c["historical_reliability_score"])
            counts["carriers"] += 1

    return counts
