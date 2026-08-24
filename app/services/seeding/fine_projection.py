"""fine_projection-exclusive seed data (fine rules, worked-example orders)
and the day-by-day scenario replay that exercises FineProjectionService."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, TypedDict

from app.core.exceptions import OrderNotFoundError
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.order import OrderRepository
from app.services.fine_projection import DELAY_VIOLATION_TYPES, SHORTAGE_VIOLATION_TYPES, FineRule
from app.services.fine_projection.service import FineProjectionService
from app.services.seeding.scenario_data_projection import (
    AMZ_RULES,
    WMT_RULES,
    amz1_days,
    amz2_days,
    wmt2_days,
    wmt_days,
)

# Source-doc references aren't part of the pure FineRule dataclass
# (deliberately -- that dataclass stays minimal/pure), so they're kept
# here as the one piece of seed-only metadata layered on top of it.
_SOURCE_DOC_REFERENCE = {
    "RULE-WMT-SHORT": "Walmart Supplier Manual v2026.1 (mock)",
    "RULE-WMT-OTIF": "Walmart Supplier Manual v2026.1 (mock)",
    "RULE-AMZ-FILL": "Amazon Vendor Central Chargeback Policy (mock)",
    "RULE-AMZ-OTIF": "Amazon Vendor Central Chargeback Policy (mock)",
}


def _rule_to_seed_dict(rule: FineRule, retailer_id: str) -> dict[str, Any]:
    """Converts a canonical WMT_RULES/AMZ_RULES FineRule (the same objects
    the pure-engine tests use) into FineRuleRepository.add_rule kwargs, so
    seed data and validated test numbers can't drift apart."""
    return {
        "rule_id": rule.rule_id,
        "retailer_id": retailer_id,
        "violation_type": rule.violation_type,
        "calc_type": rule.calc_type.value,
        "rate": rule.rate,
        "threshold_pct": rule.threshold_pct,
        "cap_amount": rule.cap_amount,
        "source_doc_reference": _SOURCE_DOC_REFERENCE.get(rule.rule_id),
    }


_RULES = [_rule_to_seed_dict(r, "RET-WMT") for r in WMT_RULES] + [
    _rule_to_seed_dict(r, "RET-AMZ") for r in AMZ_RULES
]


def _day_before_first(days) -> date:
    """order_date convention in every worked example: one day before the
    first tracked projection day (SAP order placed the day before the
    first daily snapshot we have facts for)."""
    return days[0][0].projection_date - timedelta(days=1)


class _OrderSeed(TypedDict):
    order_id: str
    retailer_id: str
    sku_id: str
    ship_from_location_id: str
    order_qty: int
    unit_price: float
    order_date: date
    requested_delivery_date: date
    required_ship_date: date
    order_status: str
    carrier_id: str


_ORDERS: list[_OrderSeed] = [
    {
        "order_id": "WMT-100234",
        "retailer_id": "RET-WMT",
        "sku_id": "SKU-PED30",
        "ship_from_location_id": "LOC-ATL",
        "order_qty": 2000,
        "unit_price": 18.0,
        "order_date": _day_before_first(wmt_days),
        "requested_delivery_date": wmt_days[0][0].requested_delivery_date,
        "required_ship_date": wmt_days[0][0].required_ship_date,
        "order_status": "OPEN",
        "carrier_id": "CAR-SWIFT",
    },
    {
        "order_id": "WMT-100511",
        "retailer_id": "RET-WMT",
        "sku_id": "SKU-CES12",
        "ship_from_location_id": "LOC-ATL",
        "order_qty": 1500,
        "unit_price": 18.0,
        "order_date": _day_before_first(wmt2_days),
        "requested_delivery_date": wmt2_days[0][0].requested_delivery_date,
        "required_ship_date": wmt2_days[0][0].required_ship_date,
        "order_status": "OPEN",
        "carrier_id": "CAR-SWIFT",
    },
    {
        "order_id": "AMZ-778501",
        "retailer_id": "RET-AMZ",
        "sku_id": "SKU-WHI20",
        "ship_from_location_id": "LOC-COL",
        "order_qty": 1200,
        "unit_price": 14.0,
        "order_date": _day_before_first(amz1_days),
        "requested_delivery_date": amz1_days[0][0].requested_delivery_date,
        "required_ship_date": amz1_days[0][0].required_ship_date,
        "order_status": "OPEN",
        "carrier_id": "CAR-JBHUNT",
    },
    {
        "order_id": "AMZ-780112",
        "retailer_id": "RET-AMZ",
        "sku_id": "SKU-WHI20",
        "ship_from_location_id": "LOC-COL",
        "order_qty": 900,
        "unit_price": 14.0,
        "order_date": _day_before_first(amz2_days),
        "requested_delivery_date": amz2_days[0][0].requested_delivery_date,
        "required_ship_date": amz2_days[0][0].required_ship_date,
        "order_status": "OPEN",
        "carrier_id": "CAR-JBHUNT",
    },
]
_SCENARIOS = [
    ("WMT-100234", wmt_days),
    ("WMT-100511", wmt2_days),
    ("AMZ-778501", amz1_days),
    ("AMZ-780112", amz2_days),
]


def seed(rules: FineRuleRepository, orders: OrderRepository) -> dict[str, int]:
    """Idempotent: safe to call repeatedly. Skips anything that already
    exists rather than erroring on a duplicate key."""
    counts = {"rules": 0, "orders": 0}

    existing_rules = {r["rule_id"] for r in rules.list_rules()}
    for rule_dict in _RULES:
        if rule_dict["rule_id"] not in existing_rules:
            rules.add_rule(**rule_dict)
            counts["rules"] += 1

    for o in _ORDERS:
        if orders.get_order(o["order_id"]) is None:
            orders.create_order(**o)
            counts["orders"] += 1

    return counts


def simulate_daily_run(orders: OrderRepository, projection_service: FineProjectionService) -> list[dict]:
    """Walks all four scenarios day by day: writes each day's facts, runs
    the projection, and marks the order DELIVERED after its final day."""
    summaries = []
    production_seq = 0
    for order_id, days in _SCENARIOS:
        order = orders.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id, hint="Call seed_master_data() first.")

        daily_results = []
        for snapshot, note in days:
            orders.add_confirmation(
                order_id=order_id,
                confirmation_id=f"CONF-{order_id}-{snapshot.projection_date.isoformat()}",
                confirmed_qty=snapshot.confirmed_qty,
                confirmation_date=datetime.combine(snapshot.projection_date, datetime.min.time()),
            )
            production_seq += 1
            orders.add_production_status(
                production_id=f"PROD-{order['sku_id']}-{order['ship_from_location_id']}-"
                f"{snapshot.projection_date.isoformat()}-{production_seq:04d}",
                sku_id=order["sku_id"],
                location_id=order["ship_from_location_id"],
                status=snapshot.production_status.value,
                status_date=datetime.combine(snapshot.projection_date, datetime.min.time()),
            )
            if snapshot.demand_exception_flagged:
                orders.add_demand_exception(
                    exception_id=f"EXC-{order_id}-{snapshot.projection_date.isoformat()}",
                    order_id=order_id,
                    flagged_date=snapshot.projection_date,
                )
            orders.record_shipment_event(
                order_id=order_id,
                carrier_id=order["carrier_id"],
                expected_ship_date=snapshot.expected_ship_date,
                actual_ship_date=snapshot.actual_ship_date,
                appointment_status=snapshot.appointment_status.value,
                expected_transit_days=snapshot.expected_transit_days,
                recorded_at=datetime.combine(snapshot.projection_date, datetime.min.time()),
            )

            result = projection_service.run_for_order(order_id, snapshot.projection_date)
            # Per-model dollar breakdown, not just the combined total --
            # summed across whichever violations happen to be shortage-
            # vs. delay-priced for this retailer's rule set (usually one
            # of each, but this doesn't assume that -- a retailer with
            # two shortage-type rules active would sum both correctly).
            shortage_fine = sum(
                v.expected_fine for v in result.violations if v.violation_type in SHORTAGE_VIOLATION_TYPES
            )
            delay_fine = sum(
                v.expected_fine for v in result.violations if v.violation_type in DELAY_VIOLATION_TYPES
            )
            daily_results.append(
                {
                    "projection_date": result.projection_date,
                    "note": note,
                    "shortage_fine": round(shortage_fine, 2),
                    "delay_fine": round(delay_fine, 2),
                    "total_expected_fine": result.total_expected_fine,
                    "shortage_probability": result.shortage_probability,
                    "delay_probability": result.delay_probability,
                }
            )

        orders.set_order_status(order_id, "DELIVERED")
        summaries.append({"order_id": order_id, "days": daily_results})

    return summaries
