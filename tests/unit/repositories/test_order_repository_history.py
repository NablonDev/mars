"""
Tests for the four read-only history methods added to OrderRepository
for the fine-summary feature: list_confirmations,
list_shipments, list_demand_exceptions, list_production_status_history.

Unlike OrderRepository.build_snapshot (latest-as-of-a-date), these
return the FULL history, oldest first -- see
docs/FINE_ENGINE.md and app/repositories/order.py.

list_production_status_history in particular must surface BOTH orders'
rows on a shared (sku_id, location_id), not filter to "this order's
own" -- reusing the same shared-plant scenario as
tests/test_known_limitations.py, since the fine-summary layer's caveat
logic depends on seeing the honest, unfiltered data.
"""

from datetime import date, datetime


def test_list_confirmations_returns_full_history_oldest_first(services):
    services.master_data.add_retailer("RET-X", "Retailer X", None, "SUM")
    services.master_data.add_sku("SKU-X", "MAT-X", None)
    services.master_data.add_location("LOC-X", None, None)
    services.orders.create_order(
        order_id="ORD-HIST",
        retailer_id="RET-X",
        sku_id="SKU-X",
        ship_from_location_id="LOC-X",
        order_qty=1000,
        unit_price=10.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    services.orders.add_confirmation(
        order_id="ORD-HIST",
        confirmation_id="CONF-ORD-HIST-02",
        confirmed_qty=950,
        confirmation_date=datetime(2026, 8, 3),
    )
    services.orders.add_confirmation(
        order_id="ORD-HIST",
        confirmation_id="CONF-ORD-HIST-01",
        confirmed_qty=1000,
        confirmation_date=datetime(2026, 8, 2),
    )

    history = services.orders.list_confirmations("ORD-HIST")

    assert [h["confirmation_id"] for h in history] == ["CONF-ORD-HIST-01", "CONF-ORD-HIST-02"]
    assert [h["confirmed_qty"] for h in history] == [1000, 950]


def test_list_shipments_returns_full_history_oldest_first(services):
    services.master_data.add_retailer("RET-X", "Retailer X", None, "SUM")
    services.master_data.add_sku("SKU-X", "MAT-X", None)
    services.master_data.add_location("LOC-X", None, None)
    services.orders.create_order(
        order_id="ORD-SHIP",
        retailer_id="RET-X",
        sku_id="SKU-X",
        ship_from_location_id="LOC-X",
        order_qty=1000,
        unit_price=10.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    services.orders.record_shipment_event(
        order_id="ORD-SHIP",
        carrier_id=None,
        expected_ship_date=date(2026, 8, 8),
        actual_ship_date=None,
        appointment_status="SCHEDULED",
        expected_transit_days=2,
        recorded_at=datetime(2026, 8, 5),
    )
    services.orders.record_shipment_event(
        order_id="ORD-SHIP",
        carrier_id=None,
        expected_ship_date=date(2026, 8, 9),
        actual_ship_date=None,
        appointment_status="MISSED",
        expected_transit_days=2,
        recorded_at=datetime(2026, 8, 8),
    )

    history = services.orders.list_shipments("ORD-SHIP")

    assert len(history) == 2
    assert history[0]["recorded_at"] < history[1]["recorded_at"]
    assert history[0]["appointment_status"] == "SCHEDULED"
    assert history[1]["appointment_status"] == "MISSED"


def test_list_demand_exceptions_returns_full_history_oldest_first(services):
    services.master_data.add_retailer("RET-X", "Retailer X", None, "SUM")
    services.master_data.add_sku("SKU-X", "MAT-X", None)
    services.master_data.add_location("LOC-X", None, None)
    services.orders.create_order(
        order_id="ORD-EXC",
        retailer_id="RET-X",
        sku_id="SKU-X",
        ship_from_location_id="LOC-X",
        order_qty=1000,
        unit_price=10.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    services.orders.add_demand_exception(
        exception_id="EXC-2", order_id="ORD-EXC", flagged_date=date(2026, 8, 4)
    )
    services.orders.add_demand_exception(
        exception_id="EXC-1", order_id="ORD-EXC", flagged_date=date(2026, 8, 2)
    )

    history = services.orders.list_demand_exceptions("ORD-EXC")

    assert [h["exception_id"] for h in history] == ["EXC-1", "EXC-2"]


def test_list_production_status_history_raises_for_unknown_order(services):
    import pytest

    from app.core.exceptions import OrderNotFoundError

    with pytest.raises(OrderNotFoundError, match="ORD-NOPE"):
        services.orders.list_production_status_history("ORD-NOPE")


def test_list_production_status_history_surfaces_both_orders_on_shared_line(services):
    """Same shared-plant scenario as
    tests/test_known_limitations.py::test_shared_production_line_backfill_is_order_dependent_not_order_specific,
    but proving list_production_status_history's honesty instead of
    build_snapshot's single-latest-row behavior: both AMZ-A's and AMZ-B's
    rows for the shared (sku_id, location_id) must show up when either
    order's history is queried, because fact_production_schedule has no
    order_id column at all -- a production line serves whichever orders
    draw on it. Filtering this down to "this order's own" would hide
    exactly the caveat the fine-summary layer needs to flag."""
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
    services.orders.add_production_status(
        production_id="PROD-A-001",
        sku_id="SKU-WHI20",
        location_id="LOC-COL",
        status="BEHIND",
        status_date=datetime.combine(shared_day, datetime.min.time()),
    )
    services.orders.add_production_status(
        production_id="PROD-B-001",
        sku_id="SKU-WHI20",
        location_id="LOC-COL",
        status="ON_TRACK",
        status_date=datetime.combine(shared_day, datetime.min.time()),
    )

    history_from_a = services.orders.list_production_status_history("AMZ-A")
    history_from_b = services.orders.list_production_status_history("AMZ-B")

    # Both orders see the SAME two rows -- neither view is filtered down
    # to "its own" production_id.
    assert history_from_a == history_from_b
    production_ids = {row["production_id"] for row in history_from_a}
    assert production_ids == {"PROD-A-001", "PROD-B-001"}
    statuses_on_shared_day = {row["status"] for row in history_from_a}
    assert statuses_on_shared_day == {"BEHIND", "ON_TRACK"}
    for row in history_from_a:
        assert row["sku_id"] == "SKU-WHI20"
        assert row["location_id"] == "LOC-COL"
