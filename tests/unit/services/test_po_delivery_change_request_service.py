"""Tests for PoDeliveryChangeRequestService: create/accept/counter/reject/expire, and one
full-lifecycle test (create -> accept -> Order.current_delivery_date updated -> a
fresh ProjectedFine reflects the new date) against the SQLite test DB.

Dates are computed relative to date.today() (not hardcoded, unlike the seeded demo
orders in app/services/seeding/, which are fixed in 2026-08 and would otherwise drift
stale relative to the lead-time gate as real time passes).
"""

from datetime import date, timedelta

import pytest

from app.core.exceptions import (
    ActivePoDeliveryChangeRequestExistsError,
    InvalidPoDeliveryChangeResponseError,
    OrderNotFoundError,
    PoDeliveryChangeLeadTimeError,
    PoDeliveryChangeRequestNotFoundError,
)

_ORDER_QTY = 1000
_UNIT_PRICE = 10.0


def _seed_order(
    services,
    order_id: str,
    *,
    required_ship_date: date,
    requested_delivery_date: date,
    retailer_id: str = "RET-EXT",
    extension_min_lead_days: int = 2,
    extension_response_sla_hours: int = 48,
    extension_fine_threshold: float = 0.0,
) -> None:
    """Every test gets a fresh function-scoped `services`/`database` fixture (see
    tests/conftest.py), so this always seeds fresh reference data -- no
    exists-check needed. sku_id/location_id are derived from retailer_id so
    tests that seed more than one retailer in a single test function (e.g.
    the per-retailer-policy and negotiation-status-lifecycle tests) don't
    collide on a shared "SKU-EXT"/"LOC-EXT" natural key."""
    sku_id = f"SKU-{retailer_id}"
    location_id = f"LOC-{retailer_id}"
    services.master_data.add_retailer(
        retailer_id,
        "Extension Test Retailer",
        None,
        "SUM",
        extension_min_lead_days,
        extension_response_sla_hours,
        extension_fine_threshold,
    )
    services.master_data.add_sku(sku_id, "MAT-EXT", None)
    services.master_data.add_location(location_id, None, None)
    services.rules.add_rule(
        rule_id=f"RULE-{retailer_id}",
        retailer_id=retailer_id,
        violation_type="OTIF_LATE",
        calc_type="FLAT_FEE",
        rate=25.0,
    )

    services.orders.create_order(
        order_id=order_id,
        retailer_id=retailer_id,
        sku_id=sku_id,
        ship_from_location_id=location_id,
        order_qty=_ORDER_QTY,
        unit_price=_UNIT_PRICE,
        order_date=date.today(),  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
        requested_delivery_date=requested_delivery_date,
        required_ship_date=required_ship_date,
    )


def test_create_request_success(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    _seed_order(
        services,
        "ORD-EXT-1",
        required_ship_date=today + timedelta(days=10),
        requested_delivery_date=today + timedelta(days=12),
    )

    row = services.po_delivery_change_service.create_request(
        "ORD-EXT-1", "DELAY", today + timedelta(days=16), notes="ops requested more time"
    )

    assert row["status"] == "PENDING"
    assert row["order_id"] == "ORD-EXT-1"
    assert row["reason_code"] == "DELAY"
    assert row["proposed_delivery_date"] == today + timedelta(days=16)
    assert row["baseline_delivery_date"] == today + timedelta(days=12)
    assert row["expires_at"] - row["requested_at"] == timedelta(hours=48)


def test_create_request_rejects_duplicate_active(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    _seed_order(
        services,
        "ORD-EXT-2",
        required_ship_date=today + timedelta(days=10),
        requested_delivery_date=today + timedelta(days=12),
    )
    services.po_delivery_change_service.create_request("ORD-EXT-2", "DELAY", today + timedelta(days=16))

    with pytest.raises(ActivePoDeliveryChangeRequestExistsError):
        services.po_delivery_change_service.create_request("ORD-EXT-2", "DELAY", today + timedelta(days=17))


def test_create_request_rejects_insufficient_lead_time(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    _seed_order(
        services,
        "ORD-EXT-3",
        required_ship_date=today,  # 0 days lead, default extension_min_lead_days=2
        requested_delivery_date=today + timedelta(days=2),
    )

    with pytest.raises(PoDeliveryChangeLeadTimeError):
        services.po_delivery_change_service.create_request("ORD-EXT-3", "SHORTAGE", today + timedelta(days=6))


def test_create_request_uses_per_retailer_policy(services):
    """Two retailers with different extension_min_lead_days/response_sla_hours
    get different behavior from the service -- proves the policy is read
    per-retailer from Retailer via MasterDataRepository.get_extension_policy,
    not hardcoded/global (Settings no longer has these fields at all)."""
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)

    # Retailer A: relaxed policy -- 1 day lead time is enough, 24h SLA.
    _seed_order(
        services,
        "ORD-EXT-POLICY-A",
        required_ship_date=today + timedelta(days=1),
        requested_delivery_date=today + timedelta(days=3),
        retailer_id="RET-POLICY-A",
        extension_min_lead_days=1,
        extension_response_sla_hours=24,
    )
    row_a = services.po_delivery_change_service.create_request(
        "ORD-EXT-POLICY-A", "DELAY", today + timedelta(days=7)
    )
    assert row_a["expires_at"] - row_a["requested_at"] == timedelta(hours=24)

    # Retailer B: strict policy -- same 1 day lead time is rejected under a
    # 5-day minimum, and a compliant order gets a 96h SLA instead of 24h.
    _seed_order(
        services,
        "ORD-EXT-POLICY-B-REJECT",
        required_ship_date=today + timedelta(days=1),
        requested_delivery_date=today + timedelta(days=3),
        retailer_id="RET-POLICY-B",
        extension_min_lead_days=5,
        extension_response_sla_hours=96,
    )
    with pytest.raises(PoDeliveryChangeLeadTimeError):
        services.po_delivery_change_service.create_request(
            "ORD-EXT-POLICY-B-REJECT", "DELAY", today + timedelta(days=7)
        )

    services.orders.create_order(
        order_id="ORD-EXT-POLICY-B-OK",
        retailer_id="RET-POLICY-B",
        sku_id="SKU-RET-POLICY-B",
        ship_from_location_id="LOC-RET-POLICY-B",
        order_qty=_ORDER_QTY,
        unit_price=_UNIT_PRICE,
        order_date=today,
        requested_delivery_date=today + timedelta(days=10),
        required_ship_date=today + timedelta(days=8),
    )
    row_b = services.po_delivery_change_service.create_request(
        "ORD-EXT-POLICY-B-OK", "DELAY", today + timedelta(days=14)
    )
    assert row_b["expires_at"] - row_b["requested_at"] == timedelta(hours=96)


def test_record_response_accepted_shifts_dates_and_retriggers_projection(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    original_delivery = today + timedelta(days=12)
    original_ship = today + timedelta(days=10)
    proposed_delivery = today + timedelta(days=16)  # +4 days

    _seed_order(
        services,
        "ORD-EXT-4",
        required_ship_date=original_ship,
        requested_delivery_date=original_delivery,
    )
    request = services.po_delivery_change_service.create_request("ORD-EXT-4", "DELAY", proposed_delivery)

    updated = services.po_delivery_change_service.record_response(request["request_id"], "ACCEPTED")

    assert updated["status"] == "ACCEPTED"
    assert updated["retailer_response_date"] == today

    order = services.orders.require_order("ORD-EXT-4")
    assert order["current_delivery_date"] == proposed_delivery
    assert order["current_required_ship_date"] == original_ship + timedelta(days=4)

    # Full-lifecycle assertion: a fresh ProjectedFine row exists reflecting the
    # new (shifted) delivery date -- run_for_order was re-triggered inline by
    # record_response, using OrderRepository.build_snapshot's COALESCE.
    history = services.projections.list_history("ORD-EXT-4")
    assert history, "record_response should have re-triggered projection"
    assert all(row["days_to_delivery"] == (proposed_delivery - today).days for row in history)


def test_record_response_countered_shifts_by_countered_delta(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    original_delivery = today + timedelta(days=12)
    original_ship = today + timedelta(days=10)
    proposed_delivery = today + timedelta(days=16)
    countered_delivery = today + timedelta(days=14)  # strictly between original and proposed, +2 days

    _seed_order(
        services,
        "ORD-EXT-5",
        required_ship_date=original_ship,
        requested_delivery_date=original_delivery,
    )
    request = services.po_delivery_change_service.create_request("ORD-EXT-5", "DELAY", proposed_delivery)

    updated = services.po_delivery_change_service.record_response(
        request["request_id"], "COUNTERED", countered_delivery_date=countered_delivery
    )

    assert updated["status"] == "COUNTERED"
    assert updated["countered_delivery_date"] == countered_delivery

    order = services.orders.require_order("ORD-EXT-5")
    assert order["current_delivery_date"] == countered_delivery
    assert order["current_required_ship_date"] == original_ship + timedelta(days=2)


def test_record_response_countered_out_of_range_rejected(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    _seed_order(
        services,
        "ORD-EXT-6",
        required_ship_date=today + timedelta(days=10),
        requested_delivery_date=today + timedelta(days=12),
    )
    request = services.po_delivery_change_service.create_request(
        "ORD-EXT-6", "DELAY", today + timedelta(days=16)
    )

    with pytest.raises(InvalidPoDeliveryChangeResponseError):
        # Not strictly between baseline_delivery_date (+12) and
        # proposed_delivery_date (+16).
        services.po_delivery_change_service.record_response(
            request["request_id"], "COUNTERED", countered_delivery_date=today + timedelta(days=20)
        )


def test_record_response_rejected_leaves_order_untouched(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    original_delivery = today + timedelta(days=12)
    original_ship = today + timedelta(days=10)

    _seed_order(
        services,
        "ORD-EXT-7",
        required_ship_date=original_ship,
        requested_delivery_date=original_delivery,
    )
    request = services.po_delivery_change_service.create_request(
        "ORD-EXT-7", "SHORTAGE", today + timedelta(days=16)
    )

    updated = services.po_delivery_change_service.record_response(request["request_id"], "REJECTED")

    assert updated["status"] == "REJECTED"
    order = services.orders.require_order("ORD-EXT-7")
    assert order["current_delivery_date"] is None
    assert order["current_required_ship_date"] is None

    # Projection is still re-triggered against the unchanged (original) date.
    history = services.projections.list_history("ORD-EXT-7")
    assert history
    assert all(row["days_to_delivery"] == (original_delivery - today).days for row in history)


def test_record_response_twice_raises_invalid_response(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    _seed_order(
        services,
        "ORD-EXT-8",
        required_ship_date=today + timedelta(days=10),
        requested_delivery_date=today + timedelta(days=12),
    )
    request = services.po_delivery_change_service.create_request(
        "ORD-EXT-8", "DELAY", today + timedelta(days=16)
    )
    services.po_delivery_change_service.record_response(request["request_id"], "REJECTED")

    with pytest.raises(InvalidPoDeliveryChangeResponseError):
        services.po_delivery_change_service.record_response(request["request_id"], "ACCEPTED")


def test_record_response_unknown_request_id_raises_not_found(services):
    with pytest.raises(PoDeliveryChangeRequestNotFoundError):
        services.po_delivery_change_service.record_response("ext_does_not_exist", "ACCEPTED")


def test_expire_stale_transitions_pending_past_timeout_and_retriggers(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    original_delivery = today + timedelta(days=12)
    original_ship = today + timedelta(days=10)

    _seed_order(
        services,
        "ORD-EXT-9",
        required_ship_date=original_ship,
        requested_delivery_date=original_delivery,
    )
    request = services.po_delivery_change_service.create_request(
        "ORD-EXT-9", "DELAY", today + timedelta(days=16)
    )

    # Still PENDING well before the default 48h SLA elapses.
    assert (
        services.po_delivery_change_service.expire_stale(as_of=request["requested_at"] + timedelta(hours=1))
        == []
    )

    expire_as_of = request["requested_at"] + timedelta(hours=49)
    expired = services.po_delivery_change_service.expire_stale(as_of=expire_as_of)

    assert len(expired) == 1
    assert expired[0]["request_id"] == request["request_id"]
    assert expired[0]["status"] == "EXPIRED"

    # No active PENDING request remains, and the order's dates are untouched.
    assert services.po_delivery_change_requests.find_active_for_order("ORD-EXT-9") is None
    order = services.orders.require_order("ORD-EXT-9")
    assert order["current_delivery_date"] is None

    # The re-triggered projection anchors on `as_of`'s date, not wall-clock
    # "today" -- see PoDeliveryChangeRequestService.expire_stale's
    # projection_date=resolved_as_of.date() fix.
    history = services.projections.list_history("ORD-EXT-9")
    assert history
    assert all(row["days_to_delivery"] == (original_delivery - expire_as_of.date()).days for row in history)


def test_expire_stale_defaults_as_of_to_now(services):
    assert services.po_delivery_change_service.expire_stale() == []


def test_create_request_requires_existing_order(services):
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    with pytest.raises(OrderNotFoundError):
        services.po_delivery_change_service.create_request(
            "does-not-exist", "OTHER", today + timedelta(days=5)
        )


def test_negotiation_status_transitions_through_full_lifecycle(services):
    """Order.negotiation_status is a denormalized, single-writer column
    (see the comment on Order.negotiation_status and
    OrderRepository.update_negotiation_status) -- this asserts every
    transition PoDeliveryChangeRequestService is responsible for, checked
    after each service call rather than only at the DB level: create ->
    PENDING, accept -> ACCEPTED, counter -> COUNTERED, reject -> REJECTED,
    and the sweep path -> EXPIRED. No other service in this codebase calls
    OrderRepository.update_negotiation_status (grepped)."""
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)
    original_delivery = today + timedelta(days=12)
    original_ship = today + timedelta(days=10)
    proposed_delivery = today + timedelta(days=16)

    # A brand-new order starts at NONE.
    _seed_order(
        services,
        "ORD-EXT-NEG-1",
        required_ship_date=original_ship,
        requested_delivery_date=original_delivery,
    )
    assert services.orders.require_order("ORD-EXT-NEG-1")["negotiation_status"] == "NONE"

    # create_request -> PENDING
    request = services.po_delivery_change_service.create_request("ORD-EXT-NEG-1", "DELAY", proposed_delivery)
    assert services.orders.require_order("ORD-EXT-NEG-1")["negotiation_status"] == "PENDING"

    # record_response(ACCEPTED) -> ACCEPTED
    services.po_delivery_change_service.record_response(request["request_id"], "ACCEPTED")
    assert services.orders.require_order("ORD-EXT-NEG-1")["negotiation_status"] == "ACCEPTED"

    # A second order, walked through COUNTERED.
    _seed_order(
        services,
        "ORD-EXT-NEG-2",
        required_ship_date=original_ship,
        requested_delivery_date=original_delivery,
        retailer_id="RET-EXT-NEG-2",
    )
    request_2 = services.po_delivery_change_service.create_request(
        "ORD-EXT-NEG-2", "DELAY", proposed_delivery
    )
    assert services.orders.require_order("ORD-EXT-NEG-2")["negotiation_status"] == "PENDING"
    services.po_delivery_change_service.record_response(
        request_2["request_id"], "COUNTERED", countered_delivery_date=today + timedelta(days=14)
    )
    assert services.orders.require_order("ORD-EXT-NEG-2")["negotiation_status"] == "COUNTERED"

    # A third order, walked through REJECTED.
    _seed_order(
        services,
        "ORD-EXT-NEG-3",
        required_ship_date=original_ship,
        requested_delivery_date=original_delivery,
        retailer_id="RET-EXT-NEG-3",
    )
    request_3 = services.po_delivery_change_service.create_request(
        "ORD-EXT-NEG-3", "DELAY", proposed_delivery
    )
    services.po_delivery_change_service.record_response(request_3["request_id"], "REJECTED")
    assert services.orders.require_order("ORD-EXT-NEG-3")["negotiation_status"] == "REJECTED"

    # A fourth order, walked through the sweep -> EXPIRED path.
    _seed_order(
        services,
        "ORD-EXT-NEG-4",
        required_ship_date=original_ship,
        requested_delivery_date=original_delivery,
        retailer_id="RET-EXT-NEG-4",
    )
    request_4 = services.po_delivery_change_service.create_request(
        "ORD-EXT-NEG-4", "DELAY", proposed_delivery
    )
    assert services.orders.require_order("ORD-EXT-NEG-4")["negotiation_status"] == "PENDING"
    services.po_delivery_change_service.expire_stale(as_of=request_4["requested_at"] + timedelta(hours=49))
    assert services.orders.require_order("ORD-EXT-NEG-4")["negotiation_status"] == "EXPIRED"
