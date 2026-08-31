"""
Tests for FulfillmentRepository's read-only history methods. Was
tests/unit/repositories/test_order_repository_history.py against the old
single-table `OrderRepository` -- relocated onto the split
common/{purchase_order,fulfillment} repositories, keyed on the new
header/line schema.

Unlike FulfillmentRepository's `get_latest_*_not_after` methods
(latest-as-of-a-date), the `list_*`/`list_*_for_*` methods here return the
FULL history, oldest first.

`list_production_schedule_for_material_plant` in particular must surface
BOTH orders' rows on a shared (material_id, plant_id), not filter to "this
order's own" -- reusing the same shared-plant scenario as
tests/test_known_limitations.py, since the penalty-projection-summary layer's
caveat logic depends on seeing the honest, unfiltered data.

Dropped from the original file (no direct analog in the new schema):
`test_list_production_status_history_raises_for_unknown_order` --
`list_production_schedule_for_material_plant` is keyed directly on
(material_id, plant_id), not on a purchase order id, so there is no PO
lookup step left to raise `NotFoundError(code="PO_NOT_FOUND")` from.
"""

from datetime import UTC, date, datetime, timedelta


def _seed_purchase_order(repos, number: str) -> dict:
    retailer = repos.master_data.add_retailer(f"RET-{number}", "Retailer", None, "SUM")
    material = repos.master_data.add_material(f"MAT-{number}", None)
    plant = repos.master_data.add_plant(f"PLANT-{number}", None, None)
    purchase_order = repos.purchase_orders.create_purchase_order(
        purchase_order_number=number,
        retailer_id=retailer["id"],
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    line = repos.purchase_orders.add_line(
        purchase_order_id=purchase_order["id"],
        line_number="10",
        ordered_quantity=1000,
        unit_price=25.00,
        material_id=material["id"],
        plant_id=plant["id"],
    )
    return {"purchase_order": purchase_order, "line": line, "material": material, "plant": plant}


def test_list_confirmation_lines_returns_full_history_oldest_first(repos):
    seeded = _seed_purchase_order(repos, "ORD-HIST")
    line_id = seeded["line"]["id"]

    confirmation_2 = repos.fulfillment.add_order_confirmation(
        confirmation_number="CONF-ORD-HIST-02",
        purchase_order_id=seeded["purchase_order"]["id"],
        confirmation_date=datetime(2026, 8, 3, tzinfo=UTC),
    )
    repos.fulfillment.add_order_confirmation_line(
        order_confirmation_id=confirmation_2["id"], purchase_order_line_id=line_id, confirmed_quantity=950
    )
    confirmation_1 = repos.fulfillment.add_order_confirmation(
        confirmation_number="CONF-ORD-HIST-01",
        purchase_order_id=seeded["purchase_order"]["id"],
        confirmation_date=datetime(2026, 8, 2, tzinfo=UTC),
    )
    repos.fulfillment.add_order_confirmation_line(
        order_confirmation_id=confirmation_1["id"], purchase_order_line_id=line_id, confirmed_quantity=1000
    )

    history = repos.fulfillment.list_confirmation_lines_for_line(line_id)

    assert [h["confirmed_quantity"] for h in history] == [1000, 950]


def test_list_shipments_returns_full_history_oldest_first(repos):
    seeded = _seed_purchase_order(repos, "ORD-SHIP")
    delivery = repos.fulfillment.add_delivery(
        delivery_number="DELIV-ORD-SHIP", purchase_order_id=seeded["purchase_order"]["id"]
    )
    repos.fulfillment.add_shipment(
        shipment_number="SHIP-ORD-SHIP-1",
        delivery_id=delivery["id"],
        expected_ship_date=date(2026, 8, 8),
        appointment_status="SCHEDULED",
        recorded_at=datetime(2026, 8, 5, tzinfo=UTC),
    )
    repos.fulfillment.add_shipment(
        shipment_number="SHIP-ORD-SHIP-2",
        delivery_id=delivery["id"],
        expected_ship_date=date(2026, 8, 9),
        appointment_status="MISSED",
        recorded_at=datetime(2026, 8, 8, tzinfo=UTC),
    )

    history = repos.fulfillment.list_shipments_for_purchase_order(seeded["purchase_order"]["id"])

    assert len(history) == 2
    assert history[0]["recorded_at"] < history[1]["recorded_at"]
    assert history[0]["appointment_status"] == "SCHEDULED"
    assert history[1]["appointment_status"] == "MISSED"


def test_list_demand_exceptions_returns_full_history_oldest_first(repos):
    seeded = _seed_purchase_order(repos, "ORD-EXC")
    line_id = seeded["line"]["id"]

    repos.fulfillment.add_demand_exception(
        exception_id="EXC-2", purchase_order_line_id=line_id, flagged_date=date(2026, 8, 4)
    )
    repos.fulfillment.add_demand_exception(
        exception_id="EXC-1", purchase_order_line_id=line_id, flagged_date=date(2026, 8, 2)
    )

    history = repos.fulfillment.list_demand_exceptions_for_line(line_id)

    assert [h["exception_id"] for h in history] == ["EXC-1", "EXC-2"]


def test_list_production_schedule_surfaces_both_orders_on_shared_line(repos):
    """Same shared-plant scenario as
    tests/test_known_limitations.py::test_shared_production_line_backfill_is_order_dependent_not_order_specific,
    but proving list_production_schedule_for_material_plant's honesty
    instead of a latest-single-row lookup: both AMZ-A's and AMZ-B's rows
    for the shared (material_id, plant_id) must show up when either
    order's material/plant is queried, because production_schedule has no
    purchase_order_id column at all -- a production line serves whichever
    orders draw on it. Filtering this down to "this order's own" would
    hide exactly the caveat the penalty-projection-summary layer needs to
    flag."""
    retailer = repos.master_data.add_retailer("RET-AMZ", "Amazon", "TIER_1", "SUM")
    material = repos.master_data.add_material("MAT-100587", "Whiskas")
    plant = repos.master_data.add_plant("PLANT-COL", "Plant", None)

    po_a = repos.purchase_orders.create_purchase_order(
        purchase_order_number="AMZ-A",
        retailer_id=retailer["id"],
        order_date=date(2026, 8, 2),
        requested_delivery_date=date(2026, 8, 14),
        required_ship_date=date(2026, 8, 12),
    )
    repos.purchase_orders.add_line(
        purchase_order_id=po_a["id"],
        line_number="10",
        ordered_quantity=1200,
        unit_price=25.00,
        material_id=material["id"],
        plant_id=plant["id"],
    )
    po_b = repos.purchase_orders.create_purchase_order(
        purchase_order_number="AMZ-B",
        retailer_id=retailer["id"],
        order_date=date(2026, 8, 10),
        requested_delivery_date=date(2026, 8, 18),
        required_ship_date=date(2026, 8, 16),
    )
    repos.purchase_orders.add_line(
        purchase_order_id=po_b["id"],
        line_number="10",
        ordered_quantity=900,
        unit_price=25.00,
        material_id=material["id"],
        plant_id=plant["id"],
    )

    shared_day = date(2026, 8, 12)
    repos.fulfillment.add_production_schedule(
        material_id=material["id"],
        plant_id=plant["id"],
        status="BEHIND",
        status_at=datetime.combine(shared_day, datetime.min.time()),
    )
    repos.fulfillment.add_production_schedule(
        material_id=material["id"],
        plant_id=plant["id"],
        status="ON_TRACK",
        # +1s: production_schedule has no distinguishing business-key column
        # (see the fulfillment repository's module docstring) -- its
        # re-derived natural key is (material_id, plant_id, status_at), so
        # two rows for the same material/plant/day need distinct instants
        # to both persist, unlike the pre-restructure production_id-keyed
        # table this scenario originally exercised.
        status_at=datetime.combine(shared_day, datetime.min.time()) + timedelta(seconds=1),
    )

    history = repos.fulfillment.list_production_schedule_for_material_plant(material["id"], plant["id"])

    assert len(history) == 2
    statuses = {row["status"] for row in history}
    assert statuses == {"BEHIND", "ON_TRACK"}
    for row in history:
        assert row["material_id"] == material["id"]
        assert row["plant_id"] == plant["id"]
