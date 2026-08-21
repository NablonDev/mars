"""Unit tests for app/services/fine_mitigation/ (pure engine) and
app/repositories/mitigation.py (DB round-trip), run against in-memory
SQLite (see tests/conftest.py)."""

from datetime import date

from sqlalchemy import select

from app.models import MitigationInput
from app.repositories.mitigation import MitigationRepository
from app.services.fine_mitigation import MitigationInputs, ShortageCause, evaluate_mitigation_options
from app.services.fine_mitigation.scenario_data import (
    EXPENSIVE_CARRIER_INPUTS,
    EXPENSIVE_CARRIER_RULES,
    EXPENSIVE_CARRIER_SNAPSHOT,
    MIXED_SHORTAGE_DELAY_INPUTS,
    MIXED_SHORTAGE_DELAY_RULES,
    MIXED_SHORTAGE_DELAY_SNAPSHOT,
    SEEDED_MITIGATION_INPUTS,
)
from app.services.fine_projection import (
    SHORTAGE_VIOLATION_TYPES,
    CalcType,
    FineRule,
    OrderSnapshot,
    ProductionStatus,
    project_order,
)
from app.services.fine_projection.scenario_data import AMZ_RULES, WMT_RULES


def _options_by_action(options):
    return {o.action: o for o in options}


def test_not_present_tier_only_accept_is_eligible():
    """AMZ-778501's real assignment: no MitigationInputs row at all --
    caller passes the all-default MitigationInputs, same as what
    MitigationRepository.get_inputs returns for an absent order. Real
    shortfall exists (AT_RISK, anticipated) but confirmed_qty == order_qty
    keeps SPLIT_SHIPMENT ineligible too, and no cost data means
    SPEED_UP_PRODUCTION/FASTER_CARRIER are hard-excluded -- ACCEPT alone."""
    snapshot = OrderSnapshot(
        order_id="AMZ-778501",
        projection_date=date(2026, 8, 4),
        order_qty=1200,
        unit_price=14.0,
        requested_delivery_date=date(2026, 8, 14),
        required_ship_date=date(2026, 8, 12),
        confirmed_qty=1200,
        production_status=ProductionStatus.AT_RISK,
    )
    projection = project_order(snapshot, AMZ_RULES)
    inputs = MitigationInputs(order_id="AMZ-778501")  # the "not present" default

    options = evaluate_mitigation_options(snapshot, AMZ_RULES, projection, inputs)

    assert {o.action for o in options} == {"ACCEPT"}
    assert options[0].net_saving == 0.0


def test_partial_estimated_tier_gives_estimated_medium_risk_options():
    """WMT-100511: cause/costs known, none confirmed -- both
    SPEED_UP_PRODUCTION and FASTER_CARRIER should surface as ESTIMATED/MEDIUM."""
    snapshot = OrderSnapshot(
        order_id="WMT-100511",
        projection_date=date(2026, 8, 11),
        order_qty=1500,
        unit_price=18.0,
        requested_delivery_date=date(2026, 8, 15),
        required_ship_date=date(2026, 8, 13),
        confirmed_qty=1440,  # confirmed 60-unit shortfall
        production_status=ProductionStatus.AT_RISK,
    )
    projection = project_order(snapshot, WMT_RULES)
    inputs = SEEDED_MITIGATION_INPUTS["WMT-100511"]

    options = evaluate_mitigation_options(snapshot, WMT_RULES, projection, inputs)
    by_action = _options_by_action(options)

    assert by_action["SPEED_UP_PRODUCTION"].confidence == "ESTIMATED"
    assert by_action["SPEED_UP_PRODUCTION"].risk_level == "MEDIUM"
    assert by_action["FASTER_CARRIER"].confidence == "ESTIMATED"
    assert by_action["FASTER_CARRIER"].risk_level == "MEDIUM"
    assert "ACCEPT" in by_action


def test_full_confirmed_tier_gives_confirmed_low_risk_options():
    """WMT-100234: every field known and confirmed, comfortable time
    margin -- SPEED_UP_PRODUCTION and FASTER_CARRIER both CONFIRMED/LOW."""
    snapshot = OrderSnapshot(
        order_id="WMT-100234",
        projection_date=date(2026, 8, 4),
        order_qty=2000,
        unit_price=18.0,
        requested_delivery_date=date(2026, 8, 11),
        required_ship_date=date(2026, 8, 9),
        confirmed_qty=1900,  # confirmed 100-unit shortfall, 5 days available
        production_status=ProductionStatus.AT_RISK,
    )
    projection = project_order(snapshot, WMT_RULES)
    inputs = SEEDED_MITIGATION_INPUTS["WMT-100234"]

    options = evaluate_mitigation_options(snapshot, WMT_RULES, projection, inputs)
    by_action = _options_by_action(options)

    speed_up = by_action["SPEED_UP_PRODUCTION"]
    assert speed_up.confidence == "CONFIRMED"
    assert speed_up.risk_level == "LOW"
    # Hand-verifiable: closes the full 100-unit shortfall (250/day x 5
    # days available >> 100) at $3.50/unit.
    assert speed_up.action_cost == 350.00

    faster_carrier = by_action["FASTER_CARRIER"]
    assert faster_carrier.confidence == "CONFIRMED"
    assert faster_carrier.action_cost == 950.00


def test_hard_exclude_tier_excludes_speed_up_production_on_cause_alone():
    """AMZ-780112: cost data is present (cheaper than WMT-100234's, even),
    but the cause is a *confirmed* RAW_MATERIAL shortage -- proves the
    exclusion is cause-based, not data-absence-based."""
    snapshot = OrderSnapshot(
        order_id="AMZ-780112",
        projection_date=date(2026, 8, 12),
        order_qty=900,
        unit_price=14.0,
        requested_delivery_date=date(2026, 8, 18),
        required_ship_date=date(2026, 8, 16),
        confirmed_qty=820,  # confirmed 80-unit shortfall
        production_status=ProductionStatus.AT_RISK,
    )
    projection = project_order(snapshot, AMZ_RULES)
    inputs = SEEDED_MITIGATION_INPUTS["AMZ-780112"]
    assert inputs.shortage_cause == ShortageCause.RAW_MATERIAL
    assert inputs.capacity_boost_cost_per_unit is not None  # data present anyway

    options = evaluate_mitigation_options(snapshot, AMZ_RULES, projection, inputs)

    assert "SPEED_UP_PRODUCTION" not in {o.action for o in options}
    assert "FASTER_CARRIER" in {o.action for o in options}  # unaffected by the shortage cause
    assert "SPLIT_SHIPMENT" in {o.action for o in options}  # confirmed_qty < order_qty


def test_accept_always_present_and_never_dropped():
    for snapshot, rules, inputs in [
        (
            EXPENSIVE_CARRIER_SNAPSHOT,
            EXPENSIVE_CARRIER_RULES,
            EXPENSIVE_CARRIER_INPUTS,
        ),
        (
            MIXED_SHORTAGE_DELAY_SNAPSHOT,
            MIXED_SHORTAGE_DELAY_RULES,
            MIXED_SHORTAGE_DELAY_INPUTS,
        ),
    ]:
        projection = project_order(snapshot, rules)
        options = evaluate_mitigation_options(snapshot, rules, projection, inputs)
        assert any(o.action == "ACCEPT" for o in options)


def test_options_sorted_by_net_saving_descending():
    projection = project_order(MIXED_SHORTAGE_DELAY_SNAPSHOT, MIXED_SHORTAGE_DELAY_RULES)
    options = evaluate_mitigation_options(
        MIXED_SHORTAGE_DELAY_SNAPSHOT, MIXED_SHORTAGE_DELAY_RULES, projection, MIXED_SHORTAGE_DELAY_INPUTS
    )

    savings = [o.net_saving for o in options]
    assert savings == sorted(savings, reverse=True)


def test_paid_option_that_costs_more_than_it_saves_still_loses_to_accept():
    """FASTER_CARRIER is structurally eligible (fully confirmed data) but
    its $999 cost dwarfs the $200 flat fee it would avoid -- ACCEPT must
    still rank first, and FASTER_CARRIER must still be present (a bad
    deal, not an ineligible one)."""
    projection = project_order(EXPENSIVE_CARRIER_SNAPSHOT, EXPENSIVE_CARRIER_RULES)
    options = evaluate_mitigation_options(
        EXPENSIVE_CARRIER_SNAPSHOT, EXPENSIVE_CARRIER_RULES, projection, EXPENSIVE_CARRIER_INPUTS
    )
    by_action = _options_by_action(options)

    assert options[0].action == "ACCEPT"
    assert "FASTER_CARRIER" in by_action
    assert by_action["FASTER_CARRIER"].net_saving < 0
    assert by_action["FASTER_CARRIER"].confidence == "CONFIRMED"


def test_split_shipment_zeroes_only_the_delay_component():
    """Mixed shortage+delay order: SPLIT_SHIPMENT's projected_fine_after
    must equal only the shortage-side violation, proving the delay
    component (OTIF_LATE) was zeroed and the shortage component wasn't."""
    projection = project_order(MIXED_SHORTAGE_DELAY_SNAPSHOT, MIXED_SHORTAGE_DELAY_RULES)
    options = evaluate_mitigation_options(
        MIXED_SHORTAGE_DELAY_SNAPSHOT, MIXED_SHORTAGE_DELAY_RULES, projection, MIXED_SHORTAGE_DELAY_INPUTS
    )
    split = _options_by_action(options)["SPLIT_SHIPMENT"]

    shortage_only_fine = sum(
        v.expected_fine for v in projection.violations if v.violation_type in SHORTAGE_VIOLATION_TYPES
    )
    assert split.projected_fine_after == round(shortage_only_fine, 2)
    assert split.projected_fine_after < projection.total_expected_fine
    assert split.action_cost == MIXED_SHORTAGE_DELAY_INPUTS.split_shipment_handling_cost


def test_faster_carrier_hard_excluded_when_no_delay_type_rule_applies():
    snapshot = OrderSnapshot(
        order_id="MIT-NO-DELAY-RULE",
        projection_date=date(2026, 8, 1),
        order_qty=1000,
        unit_price=10.0,
        requested_delivery_date=date(2026, 8, 6),
        required_ship_date=date(2026, 8, 4),
        confirmed_qty=1000,
        production_status=ProductionStatus.ON_TRACK,
    )
    rules = [FineRule("RULE-NO-DELAY", "SHORT_SHIP", CalcType.PER_UNIT, rate=3.0, threshold_pct=0.0)]
    inputs = MitigationInputs(
        order_id="MIT-NO-DELAY-RULE",
        express_carrier_cost=100.0,
        express_carrier_transit_days=1,
        express_carrier_data_confirmed=True,
    )
    projection = project_order(snapshot, rules)

    options = evaluate_mitigation_options(snapshot, rules, projection, inputs)

    assert "FASTER_CARRIER" not in {o.action for o in options}


# ---------------------------------------------------------------------
# Repository-level (DB) tests
# ---------------------------------------------------------------------


def test_get_inputs_returns_defaults_when_absent(db_session):
    repo = MitigationRepository(db_session)

    inputs = repo.get_inputs("NO-SUCH-ORDER")

    assert inputs == MitigationInputs(order_id="NO-SUCH-ORDER")


def test_upsert_then_get_round_trips(services, db_session):
    services.master_data.add_retailer("RET-MIT", "Mitigation Test Co", None)
    services.master_data.add_sku("SKU-MIT", "MAT-MIT", None)
    services.master_data.add_location("LOC-MIT", None, None)
    services.orders.create_order(
        order_id="ORD-MIT",
        retailer_id="RET-MIT",
        sku_id="SKU-MIT",
        ship_from_location_id="LOC-MIT",
        order_qty=100,
        unit_price=5.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    repo = MitigationRepository(db_session)

    repo.upsert_inputs(
        order_id="ORD-MIT",
        shortage_cause="LABOR_CAPACITY",
        shortage_cause_confirmed=True,
        capacity_boost_cost_per_unit=2.50,
        capacity_boost_max_units_per_day=50.0,
        capacity_boost_data_confirmed=True,
        express_carrier_cost=200.0,
        express_carrier_transit_days=1,
        express_carrier_data_confirmed=True,
        split_shipment_handling_cost=25.0,
    )

    inserted = repo.get_inputs("ORD-MIT")
    assert inserted.shortage_cause == ShortageCause.LABOR_CAPACITY
    assert inserted.capacity_boost_cost_per_unit == 2.50

    # Idempotent: calling again with a changed field updates in place,
    # rather than raising a duplicate-key error on order_id.
    repo.upsert_inputs(order_id="ORD-MIT", capacity_boost_cost_per_unit=9.99)
    updated = repo.get_inputs("ORD-MIT")
    assert updated.capacity_boost_cost_per_unit == 9.99
    assert updated.shortage_cause == ShortageCause.LABOR_CAPACITY  # untouched fields survive

    rows = db_session.scalars(select(MitigationInput).where(MitigationInput.order_id == "ORD-MIT")).all()
    assert len(rows) == 1  # updated in place, not duplicated
