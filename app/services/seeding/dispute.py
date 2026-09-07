"""Dispute-resolution-exclusive seed data orchestration: rules, purchase
orders, real post-delivery fulfillment facts, and `actual_penalty` charges
for the eight dispute scenarios in `scenario_data_dispute.py`. Mirrors
`app.services.seeding.projection`/`app.services.seeding.mitigation`'s
shape -- data lives in `scenario_data_dispute.py`, this module only turns
it into rows via the repository layer.
"""

from __future__ import annotations

from datetime import datetime

from app.repositories.common.fulfillment import FulfillmentRepository
from app.repositories.common.master_data import MasterDataRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.projection import ActualPenaltyRepository
from app.repositories.penalties.rule import PenaltyRuleRepository
from app.services.seeding.scenario_data_dispute import (
    MATERIAL_CODE,
    PLANT_CODE,
    RETAILER_CODES,
    RULES,
    SCENARIOS,
)


def seed(
    rules: PenaltyRuleRepository,
    purchase_orders: PurchaseOrderRepository,
    fulfillment: FulfillmentRepository,
    master_data: MasterDataRepository,
    actual_penalties: ActualPenaltyRepository,
) -> dict[str, int]:
    """Idempotent: safe to call repeatedly, skips anything that already
    exists rather than erroring on a duplicate key."""
    counts = {"dispute_rules": 0, "dispute_orders": 0, "dispute_actual_penalties": 0}

    for retailer_code in RETAILER_CODES:
        if master_data.get_retailer_by_code(retailer_code) is None:
            master_data.add_retailer(retailer_code, f"{retailer_code} Test Retailer", None, "SUM")
    if master_data.get_material_by_code(MATERIAL_CODE) is None:
        master_data.add_material(MATERIAL_CODE, None)
    if master_data.get_plant_by_code(PLANT_CODE) is None:
        master_data.add_plant(PLANT_CODE, None, None)

    existing_rule_codes = {r["rule_code"] for r in rules.list_rules()}
    for rule_fixture in RULES:
        if rule_fixture.rule_code in existing_rule_codes:
            continue
        retailer = master_data.get_retailer_by_code(rule_fixture.retailer_code)
        assert retailer is not None
        rules.add_rule(
            rule_code=rule_fixture.rule_code,
            retailer_id=retailer["id"],
            violation_type=rule_fixture.violation_type,
            calc_type=rule_fixture.calc_type,
            rate=rule_fixture.rate,
            threshold_pct=rule_fixture.threshold_pct,
            cap_amount=rule_fixture.cap_amount,
            grace_period_days=rule_fixture.grace_period_days,
            effective_start_date=rule_fixture.effective_start_date,
            effective_end_date=rule_fixture.effective_end_date,
            tiers=rule_fixture.tiers,
        )
        counts["dispute_rules"] += 1

    for scenario in SCENARIOS:
        if purchase_orders.get_by_number(scenario.purchase_order_number) is not None:
            continue

        retailer = master_data.get_retailer_by_code(scenario.retailer_code)
        material = master_data.get_material_by_code(scenario.material_code)
        plant = master_data.get_plant_by_code(scenario.plant_code)
        assert retailer is not None and material is not None and plant is not None

        purchase_order = purchase_orders.create_purchase_order(
            purchase_order_number=scenario.purchase_order_number,
            retailer_id=retailer["id"],
            order_date=scenario.order_date,
            requested_delivery_date=scenario.requested_delivery_date,
            required_ship_date=scenario.required_ship_date,
            order_status="DELIVERED",
        )
        line = purchase_orders.add_line(
            purchase_order_id=purchase_order["id"],
            line_number="10",
            ordered_quantity=scenario.order_qty,
            unit_price=scenario.unit_price,
            material_id=material["id"],
            plant_id=plant["id"],
        )

        delivery = None
        if scenario.delivered_qty is not None:
            delivery = fulfillment.add_delivery(
                delivery_number=f"DELIV-{scenario.purchase_order_number}",
                purchase_order_id=purchase_order["id"],
                actual_delivery_date=scenario.invoice_or_deduction_date,
            )
            fulfillment.add_delivery_line(
                delivery_id=delivery["id"],
                purchase_order_line_id=line["id"],
                delivered_quantity=scenario.delivered_qty,
            )

        if scenario.actual_delivery_date is not None:
            # Delay-family scenarios need a Shipment row (the delay fact
            # source, see app.repositories.common.fulfillment.
            # get_latest_shipment_for_purchase_order_not_after); a bare
            # Delivery header is still required since Shipment.delivery_id
            # is a real FK, even though the delivery's own
            # actual_delivery_date is irrelevant to a delay dispute.
            if delivery is None:
                delivery = fulfillment.add_delivery(
                    delivery_number=f"DELIV-{scenario.purchase_order_number}",
                    purchase_order_id=purchase_order["id"],
                )
            fulfillment.add_shipment(
                shipment_number=f"SHIP-{scenario.purchase_order_number}",
                delivery_id=delivery["id"],
                # recorded_at must be <= the scenario's invoice date --
                # get_latest_shipment_for_purchase_order_not_after filters
                # on it; the real (wall-clock) seeding time is irrelevant
                # to these historical scenarios.
                recorded_at=datetime.combine(scenario.actual_delivery_date, datetime.min.time()),
                actual_delivery_date=scenario.actual_delivery_date,
                expected_delivery_date=scenario.requested_delivery_date,
            )

        actual_penalties.add_actual_penalty(
            actual_penalty_number=f"AP-{scenario.purchase_order_number}",
            purchase_order_id=purchase_order["id"],
            violation_type=scenario.violation_type,
            actual_penalty_amount=scenario.claimed_amount,
            invoice_or_deduction_date=scenario.invoice_or_deduction_date,
        )
        counts["dispute_orders"] += 1
        counts["dispute_actual_penalties"] += 1

    return counts
