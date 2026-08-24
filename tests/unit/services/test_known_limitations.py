"""
Characterization tests for known, accepted (not fixed) limitations --
documenting exactly what happens today so nobody has to rediscover it by
surprise.

AMZ-778501 and AMZ-780112 (the two Amazon worked examples) both draw on
SKU-WHI20 at LOC-COL, and production_schedule is correctly keyed by
(sku_id, location_id) -- a production line serves whichever orders draw
on it, matching how real production actually works. But the two demo
scenarios were authored independently and assign that shared line
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
latest status for that sku/location as of that date.
"""

from datetime import date, datetime

from app.services.fine_projection import ProductionStatus


def test_shared_production_line_backfill_is_order_dependent_not_order_specific(services):
    services.master_data.add_retailer("RET-AMZ", "Amazon", "TIER_1", "SUM")
    services.master_data.add_sku("SKU-WHI20", "MAT-100587", "Whiskas")
    services.master_data.add_location("LOC-COL", "Plant", "PLANT")
    services.orders.create_order(
        order_id="AMZ-A",
        retailer_id="RET-AMZ",
        sku_id="SKU-WHI20",
        ship_from_location_id="LOC-COL",
        order_qty=1200,
        unit_price=14.0,
        order_date=date(2026, 8, 2),
        requested_delivery_date=date(2026, 8, 14),
        required_ship_date=date(2026, 8, 12),
    )
    services.orders.create_order(
        order_id="AMZ-B",
        retailer_id="RET-AMZ",
        sku_id="SKU-WHI20",
        ship_from_location_id="LOC-COL",
        order_qty=900,
        unit_price=14.0,
        order_date=date(2026, 8, 10),
        requested_delivery_date=date(2026, 8, 18),
        required_ship_date=date(2026, 8, 16),
    )

    shared_day = date(2026, 8, 12)
    # AMZ-A's own narrative for this shared plant/SKU on Aug 12: BEHIND.
    services.orders.add_production_status(
        production_id="PROD-A-001",
        sku_id="SKU-WHI20",
        location_id="LOC-COL",
        status="BEHIND",
        status_date=datetime.combine(shared_day, datetime.min.time()),
    )
    snapshot_right_after_a = services.orders.build_snapshot("AMZ-A", shared_day)
    assert snapshot_right_after_a.production_status == ProductionStatus.BEHIND

    # AMZ-B later writes its own (contradictory) narrative for the SAME
    # real-world plant/SKU on the SAME day: ON_TRACK.
    services.orders.add_production_status(
        production_id="PROD-B-001",
        sku_id="SKU-WHI20",
        location_id="LOC-COL",
        status="ON_TRACK",
        status_date=datetime.combine(shared_day, datetime.min.time()),
    )

    # Re-querying AMZ-A's snapshot for the SAME day now returns AMZ-B's
    # status, not AMZ-A's own -- because the table is correctly modeled
    # per real-world plant/SKU, and the two mock narratives disagree
    # about what that plant's true status was. This is the documented,
    # accepted limitation, not a surprise.
    snapshot_after_b_wrote = services.orders.build_snapshot("AMZ-A", shared_day)
    assert snapshot_after_b_wrote.production_status == ProductionStatus.ON_TRACK
    assert snapshot_after_b_wrote.production_status != snapshot_right_after_a.production_status
