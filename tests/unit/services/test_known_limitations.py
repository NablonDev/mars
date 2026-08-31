"""
Characterization tests for known, accepted (not fixed) limitations --
documenting exactly what happens today so nobody has to rediscover it by
surprise.

AMZ-778501 and AMZ-780112 (the two Amazon worked examples) both draw on
material MAT-100587 at plant LOC-COL, and production_schedule is correctly
keyed by (material_id, plant_id) -- a production line serves whichever
orders draw on it, matching how real production actually works. But the
two demo scenarios were authored independently and assign that shared line
different, contradictory statuses on their overlapping dates (Aug 11-13):
AMZ-778501 says BEHIND, AMZ-780112 says ON_TRACK. In reality a single
line has one true status per day; the mock data never reconciled this.

`test_api_admin_seed.py::test_simulate_daily_run_reproduces_published_numbers`
proves the *one specific, intended* call pattern -- seed once, simulate
once, straight through -- reproduces the published numbers exactly. That
works because each order's own facts are always the most recently written
at the moment its own projection runs (see seeding.py's docstring).

This test proves the *other* pattern -- re-running/backfilling one of the
two orders after the other has also written facts for an overlapping day
-- does NOT reliably return that order's own intended production status.
This is a property of the mock data (two independently-authored
narratives sharing a real-world resource), not a bug in the repository
query, which is doing exactly what it's documented to do: return the
latest status for that material/plant as of that date.
"""

from datetime import date, datetime, timedelta

from app.services.penalties.projection import ProductionStatus
from app.services.penalties.projection.service import ProjectionService


def test_shared_production_line_backfill_is_order_dependent_not_order_specific(repos):
    projection_service = ProjectionService(
        purchase_orders=repos.purchase_orders,
        fulfillment=repos.fulfillment,
        rules=repos.penalty_rules,
        master_data=repos.master_data,
        projections=repos.penalty_projections,
    )

    retailer = repos.master_data.add_retailer("RET-AMZ", "Amazon", "TIER_1", "SUM")
    material = repos.master_data.add_material("MAT-100587", "Whiskas")
    plant = repos.master_data.add_plant("LOC-COL", "Plant")

    purchase_order_a = repos.purchase_orders.create_purchase_order(
        purchase_order_number="AMZ-A",
        retailer_id=retailer["id"],
        order_date=date(2026, 8, 2),
        requested_delivery_date=date(2026, 8, 14),
        required_ship_date=date(2026, 8, 12),
    )
    repos.purchase_orders.add_line(
        purchase_order_id=purchase_order_a["id"],
        line_number="10",
        ordered_quantity=1200,
        unit_price=14.0,
        material_id=material["id"],
        plant_id=plant["id"],
    )
    purchase_order_b = repos.purchase_orders.create_purchase_order(
        purchase_order_number="AMZ-B",
        retailer_id=retailer["id"],
        order_date=date(2026, 8, 10),
        requested_delivery_date=date(2026, 8, 18),
        required_ship_date=date(2026, 8, 16),
    )
    repos.purchase_orders.add_line(
        purchase_order_id=purchase_order_b["id"],
        line_number="10",
        ordered_quantity=900,
        unit_price=14.0,
        material_id=material["id"],
        plant_id=plant["id"],
    )

    shared_day = date(2026, 8, 12)
    # AMZ-A's own narrative for this shared plant/material on Aug 12: BEHIND.
    repos.fulfillment.add_production_schedule(
        material_id=material["id"],
        plant_id=plant["id"],
        status="BEHIND",
        status_at=datetime.combine(shared_day, datetime.min.time()),
    )
    snapshot_right_after_a = projection_service.build_snapshot(purchase_order_a["id"], shared_day)
    assert snapshot_right_after_a.production_status == ProductionStatus.BEHIND

    # AMZ-B later writes its own (contradictory) narrative for the SAME
    # real-world plant/material on the SAME day: ON_TRACK. Needs a distinct
    # instant (+1s) since production_schedule's natural key is
    # (material_id, plant_id, status_at) -- see FulfillmentRepository's
    # module docstring.
    repos.fulfillment.add_production_schedule(
        material_id=material["id"],
        plant_id=plant["id"],
        status="ON_TRACK",
        status_at=datetime.combine(shared_day, datetime.min.time()) + timedelta(seconds=1),
    )

    # Re-querying AMZ-A's snapshot for the SAME day now returns AMZ-B's
    # status, not AMZ-A's own -- because the table is correctly modeled
    # per real-world plant/material, and the two mock narratives disagree
    # about what that plant's true status was. This is the documented,
    # accepted limitation, not a surprise.
    snapshot_after_b_wrote = projection_service.build_snapshot(purchase_order_a["id"], shared_day)
    assert snapshot_after_b_wrote.production_status == ProductionStatus.ON_TRACK
    assert snapshot_after_b_wrote.production_status != snapshot_right_after_a.production_status
