"""fine_mitigation-exclusive seed data: mitigation inputs for the
worked-example orders."""

from __future__ import annotations

from typing import Any

from app.repositories.fine_mitigation.mitigation import MitigationRepository
from app.services.seeding.scenario_data_mitigation import SEEDED_MITIGATION_INPUTS


def _mitigation_to_seed_dict(inputs: Any) -> dict[str, Any]:
    """Converts a MitigationInputs (from
    app.services.seeding.scenario_data_mitigation.SEEDED_MITIGATION_INPUTS)
    into MitigationRepository.upsert_inputs kwargs."""
    return {
        "order_id": inputs.order_id,
        "shortage_cause": inputs.shortage_cause.value,
        "shortage_cause_confirmed": inputs.shortage_cause_confirmed,
        "capacity_boost_cost_per_unit": inputs.capacity_boost_cost_per_unit,
        "capacity_boost_max_units_per_day": inputs.capacity_boost_max_units_per_day,
        "capacity_boost_data_confirmed": inputs.capacity_boost_data_confirmed,
        "express_carrier_cost": inputs.express_carrier_cost,
        "express_carrier_transit_days": inputs.express_carrier_transit_days,
        "express_carrier_data_confirmed": inputs.express_carrier_data_confirmed,
        "split_shipment_handling_cost": inputs.split_shipment_handling_cost,
    }


# AMZ-778501 has no entry in SEEDED_MITIGATION_INPUTS -- deliberate, the
# "not present" tier (MitigationRepository.get_inputs then returns all-defaults).
_MITIGATION_INPUTS = [_mitigation_to_seed_dict(inputs) for inputs in SEEDED_MITIGATION_INPUTS.values()]


def seed(mitigation: MitigationRepository) -> dict[str, int]:
    """Idempotent: safe to call repeatedly. Skips anything that already
    exists rather than erroring on a duplicate key."""
    counts = {"mitigation_inputs": 0}

    existing_mitigation_inputs = set(mitigation.list_order_ids())
    for m in _MITIGATION_INPUTS:
        if m["order_id"] not in existing_mitigation_inputs:
            mitigation.upsert_inputs(**m)
            counts["mitigation_inputs"] += 1

    return counts
