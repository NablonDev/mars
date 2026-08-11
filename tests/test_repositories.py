"""
Repository-layer tests, run against in-memory SQLite (see conftest.py).
Covers the two fixes made in this refactor round:
  - TIERED rules load their bands from dim_fine_rule_tier correctly.
  - An unrecognized calc_type raises a clear ValueError, not a bare
    KeyError from the old dict-lookup implementation.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.engine import CalcType
from app.models import (
    DemandException,
    FineRuleORM,
    FineRuleTier,
    OrderConfirmation,
    ProductionSchedule,
    Retailer,
    Shipment,
)


def test_tiered_rule_loads_its_bands(services, db_session):
    db_session.add(Retailer(retailer_id="RET-X", retailer_name="Retailer X"))
    db_session.add(
        FineRuleORM(
            rule_id="RULE-TIERED",
            retailer_id="RET-X",
            violation_type="FILL_RATE",
            calc_type="TIERED",
            rate=0.0,
            threshold_pct=0.0,
            is_active=True,
            effective_start_date=date(2026, 1, 1),
        )
    )
    db_session.add_all(
        [
            FineRuleTier(tier_id="T1", rule_id="RULE-TIERED", band_min=0.0, band_max=0.10, rate=0.02),
            FineRuleTier(tier_id="T2", rule_id="RULE-TIERED", band_min=0.10, band_max=0.30, rate=0.05),
        ]
    )
    db_session.flush()

    rules = services.rules.get_rules_for_retailer("RET-X")

    assert len(rules) == 1
    assert rules[0].calc_type == CalcType.TIERED
    assert rules[0].tiers is not None
    assert len(rules[0].tiers) == 2
    assert rules[0].tiers[0].band_min == 0.0
    assert rules[0].tiers[1].rate == 0.05


def test_unrecognized_calc_type_raises_clear_value_error(services, db_session):
    db_session.add(Retailer(retailer_id="RET-Y", retailer_name="Retailer Y"))
    db_session.add(
        FineRuleORM(
            rule_id="RULE-BAD",
            retailer_id="RET-Y",
            violation_type="SHORT_SHIP",
            calc_type="NOT_A_REAL_CALC_TYPE",
            rate=1.0,
            threshold_pct=0.0,
            is_active=True,
            effective_start_date=date(2026, 1, 1),
        )
    )
    db_session.flush()

    with pytest.raises(ValueError, match="RULE-BAD"):
        services.rules.get_rules_for_retailer("RET-Y")


def test_add_rule_via_repository_persists_tiers(services):
    services.master_data.add_retailer("RET-Z", "Retailer Z", None, "SUM")
    services.rules.add_rule(
        rule_id="RULE-Z",
        retailer_id="RET-Z",
        violation_type="FILL_RATE",
        calc_type="TIERED",
        rate=0.0,
        threshold_pct=0.0,
        cap_amount=None,
        tiers=[
            {"band_min": 0.0, "band_max": 0.10, "rate": 0.02},
            {"band_min": 0.10, "band_max": 1.01, "rate": 0.08},
        ],
    )

    rules = services.rules.get_rules_for_retailer("RET-Z")
    assert len(rules) == 1
    assert len(rules[0].tiers) == 2
    assert rules[0].tiers[1].rate == 0.08


def test_fact_writers_are_idempotent_on_their_natural_key(services, db_session):
    """Regression test for a real bug: calling each of these twice with
    the same natural-key id used to raise IntegrityError (duplicate key)
    against a real, persistent database -- exactly what
    SeedingService.simulate_daily_run does if it's ever called more than
    once (e.g. resetting a demo), since its ids are deterministic
    ("CONF-{order_id}-{date}", etc.), not something the in-memory,
    fresh-per-test SQLite database in every other test could ever expose."""
    services.master_data.add_retailer("RET-IDEM", "Idempotency Co", None)
    services.master_data.add_sku("SKU-IDEM", "MAT-IDEM", None)
    services.master_data.add_location("LOC-IDEM", None, None)
    services.orders.create_order(
        order_id="ORD-IDEM",
        retailer_id="RET-IDEM",
        sku_id="SKU-IDEM",
        ship_from_location_id="LOC-IDEM",
        order_qty=100,
        unit_price=5.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )

    for _ in range(2):
        services.orders.add_confirmation(
            order_id="ORD-IDEM",
            confirmation_id="CONF-ORD-IDEM-01",
            confirmed_qty=90,
            confirmation_date=datetime(2026, 8, 2),
        )
        services.orders.add_production_status(
            production_id="PROD-IDEM-01",
            sku_id="SKU-IDEM",
            location_id="LOC-IDEM",
            status="AT_RISK",
            status_date=datetime(2026, 8, 2),
        )
        services.orders.record_shipment_event(
            order_id="ORD-IDEM",
            carrier_id=None,
            expected_ship_date=None,
            actual_ship_date=None,
            appointment_status="SCHEDULED",
            expected_transit_days=2,
            recorded_at=datetime(2026, 8, 2),
        )
        services.orders.add_demand_exception(
            exception_id="EXC-ORD-IDEM-01", order_id="ORD-IDEM", flagged_date=date(2026, 8, 2)
        )

    confirmations = db_session.scalars(
        select(OrderConfirmation).where(OrderConfirmation.confirmation_id == "CONF-ORD-IDEM-01")
    ).all()
    productions = db_session.scalars(
        select(ProductionSchedule).where(ProductionSchedule.production_id == "PROD-IDEM-01")
    ).all()
    shipments = db_session.scalars(
        select(Shipment).where(Shipment.shipment_id == "SHIP-ORD-IDEM-20260802000000")
    ).all()
    exceptions = db_session.scalars(
        select(DemandException).where(DemandException.exception_id == "EXC-ORD-IDEM-01")
    ).all()

    assert len(confirmations) == 1
    assert len(productions) == 1
    assert len(shipments) == 1
    assert len(exceptions) == 1
