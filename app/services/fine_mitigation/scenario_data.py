"""Mock mitigation-input assignments for the four seeded demo orders, plus a
few synthetic snapshots for edge cases the four don't cover.

Numbers are chosen to be hand-verifiable, not to model a real Mars order --
same posture as app/services/fine_projection/scenario_data.py.
"""

from datetime import date

from app.services.fine_mitigation.models import MitigationInputs, ShortageCause
from app.services.fine_projection import (
    AppointmentStatus,
    CalcType,
    FineRule,
    OrderSnapshot,
    ProductionStatus,
)

# ---------------------------------------------------------------------
# The four seeded demo orders -- see app/services/fine_projection/scenario_data.py
# for their OrderSnapshot/qty/unit_price history. AMZ-778501 deliberately
# has no entry here at all: the "not present" tier. MitigationRepository
# returns all-defaults (ShortageCause.UNKNOWN, no cost data) for it, never
# an error -- see app/repositories/mitigation.py.
# ---------------------------------------------------------------------
SEEDED_MITIGATION_INPUTS: dict[str, MitigationInputs] = {
    # Full/confirmed tier: every field known and confirmed.
    "WMT-100234": MitigationInputs(
        order_id="WMT-100234",
        shortage_cause=ShortageCause.LABOR_CAPACITY,
        shortage_cause_confirmed=True,
        capacity_boost_cost_per_unit=3.50,
        capacity_boost_max_units_per_day=250,
        capacity_boost_data_confirmed=True,
        express_carrier_cost=950.00,
        express_carrier_transit_days=1,
        express_carrier_data_confirmed=True,
        split_shipment_handling_cost=150.00,
    ),
    # Partial/estimated tier: cause and costs known, none confirmed.
    "WMT-100511": MitigationInputs(
        order_id="WMT-100511",
        shortage_cause=ShortageCause.LABOR_CAPACITY,
        shortage_cause_confirmed=False,
        capacity_boost_cost_per_unit=4.25,
        capacity_boost_max_units_per_day=180,
        capacity_boost_data_confirmed=False,
        express_carrier_cost=700.00,
        express_carrier_transit_days=1,
        express_carrier_data_confirmed=False,
        split_shipment_handling_cost=100.00,
    ),
    # Hard-exclude tier: cost data present, but the cause itself
    # (confirmed RAW_MATERIAL) rules SPEED_UP_PRODUCTION out. Proves the
    # exclusion is cause-based, not data-absence-based.
    "AMZ-780112": MitigationInputs(
        order_id="AMZ-780112",
        shortage_cause=ShortageCause.RAW_MATERIAL,
        shortage_cause_confirmed=True,
        capacity_boost_cost_per_unit=3.00,
        capacity_boost_max_units_per_day=200,
        capacity_boost_data_confirmed=True,
        express_carrier_cost=600.00,
        express_carrier_transit_days=1,
        express_carrier_data_confirmed=True,
        split_shipment_handling_cost=80.00,
    ),
}

# ---------------------------------------------------------------------
# Synthetic edge case 1 -- delay-only order (no shortage), express
# carrier data fully confirmed but too expensive to be worth it. ACCEPT
# must still outrank FASTER_CARRIER on net_saving, but the option stays
# in the list (it's structurally eligible, just a bad deal).
# ---------------------------------------------------------------------
EXPENSIVE_CARRIER_SNAPSHOT = OrderSnapshot(
    order_id="MIT-EDGE-001",
    projection_date=date(2026, 8, 1),
    order_qty=500,
    unit_price=10.0,
    requested_delivery_date=date(2026, 8, 5),
    required_ship_date=date(2026, 8, 3),
    confirmed_qty=500,
    production_status=ProductionStatus.ON_TRACK,
    appointment_status=AppointmentStatus.MISSED,
    carrier_reliability_score=95.0,
    expected_transit_days=2,
)
EXPENSIVE_CARRIER_RULES = [
    FineRule("RULE-EDGE-OTIF-1", "OTIF_LATE", CalcType.FLAT_FEE, rate=200.0),
]
EXPENSIVE_CARRIER_INPUTS = MitigationInputs(
    order_id="MIT-EDGE-001",
    express_carrier_cost=999.00,  # far more than the $200 flat fee it would avoid
    express_carrier_transit_days=1,
    express_carrier_data_confirmed=True,
)

# ---------------------------------------------------------------------
# Synthetic edge case 2 -- mixed shortage + delay order. SPLIT_SHIPMENT
# must zero out only the delay (OTIF_LATE) component; the shortage
# (SHORT_SHIP) component still applies to the confirmed_qty/order_qty gap.
# ---------------------------------------------------------------------
MIXED_SHORTAGE_DELAY_SNAPSHOT = OrderSnapshot(
    order_id="MIT-EDGE-002",
    projection_date=date(2026, 8, 1),
    order_qty=1000,
    unit_price=10.0,
    requested_delivery_date=date(2026, 8, 5),
    required_ship_date=date(2026, 8, 3),
    confirmed_qty=700,  # 300-unit shortfall, but 700 ready to ship now
    production_status=ProductionStatus.ON_TRACK,
    appointment_status=AppointmentStatus.MISSED,
    carrier_reliability_score=95.0,
    expected_transit_days=2,
)
MIXED_SHORTAGE_DELAY_RULES = [
    FineRule("RULE-EDGE-SHORT-2", "SHORT_SHIP", CalcType.PER_UNIT, rate=4.0, threshold_pct=0.0),
    FineRule("RULE-EDGE-OTIF-2", "OTIF_LATE", CalcType.FLAT_FEE, rate=800.0),
]
MIXED_SHORTAGE_DELAY_INPUTS = MitigationInputs(
    order_id="MIT-EDGE-002",
    split_shipment_handling_cost=30.00,
)
