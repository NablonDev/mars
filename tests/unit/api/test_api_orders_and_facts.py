"""
Orders + fact-recording endpoints, including a direct test of the
shipment-historization fix (docs/FINE_ENGINE.md "found, not yet fixed" --
now fixed): backfilling a projection to an earlier date must reflect
that day's shipment facts, not whatever the most recent row says.
"""


def test_create_get_list_order(client):
    client.post("/api/v1/retailers", json={"retailer_id": "RET-A", "retailer_name": "A"})
    client.post("/api/v1/skus", json={"sku_id": "SKU-A", "sku_code": "MAT-A"})
    client.post("/api/v1/locations", json={"location_id": "LOC-A", "location_type": "PLANT"})

    payload = {
        "order_id": "ORD-1",
        "retailer_id": "RET-A",
        "sku_id": "SKU-A",
        "ship_from_location_id": "LOC-A",
        "order_qty": 500,
        "unit_price": 10.0,
        "order_date": "2026-08-01",
        "requested_delivery_date": "2026-08-10",
        "required_ship_date": "2026-08-08",
    }
    created = client.post("/api/v1/orders", json=payload)
    assert created.status_code == 201

    duplicate = client.post("/api/v1/orders", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "ORDER_ALREADY_EXISTS"

    fetched = client.get("/api/v1/orders/ORD-1")
    assert fetched.status_code == 200
    assert fetched.json()["order_qty"] == 500

    missing = client.get("/api/v1/orders/does-not-exist")
    assert missing.status_code == 404

    listed = client.get("/api/v1/orders")
    assert any(o["order_id"] == "ORD-1" for o in listed.json())


def test_confirmation_and_demand_exception_require_existing_order(client):
    resp = client.post(
        "/api/v1/orders/NOPE/confirmations",
        json={
            "confirmation_id": "C1",
            "confirmed_qty": 100,
            "confirmation_date": "2026-08-01T00:00:00",
        },
    )
    assert resp.status_code == 404

    resp = client.post(
        "/api/v1/orders/NOPE/demand-exceptions",
        json={
            "exception_id": "E1",
            "flagged_date": "2026-08-01",
        },
    )
    assert resp.status_code == 404


def test_shipment_history_backfills_correctly(seeded_client):
    """The core regression test for the historization fix. Two shipment
    events, recorded on different days, must each be visible only to
    projections run for that day or later -- not both averaged/overwritten
    into a single current-state row."""

    # Day 1: appointment scheduled normally.
    seeded_client.post(
        "/api/v1/orders/WMT-100234/shipments",
        json={
            "carrier_id": "CAR-SWIFT",
            "appointment_status": "SCHEDULED",
            "expected_transit_days": 2,
            "recorded_at": "2026-08-04T06:00:00",
        },
    )
    # Day 2 (later): appointment gets missed.
    seeded_client.post(
        "/api/v1/orders/WMT-100234/shipments",
        json={
            "carrier_id": "CAR-SWIFT",
            "appointment_status": "MISSED",
            "expected_transit_days": 2,
            "recorded_at": "2026-08-09T06:00:00",
        },
    )

    # Backfilled projection for Aug 5 (after the first event, before the
    # second) must see SCHEDULED, not MISSED.
    before = seeded_client.post(
        "/api/v1/orders/WMT-100234/projections",
        json={
            "projection_date": "2026-08-05",
        },
    ).json()
    delay_before = next(v for v in before["violations"] if v["violation_type"] == "OTIF_LATE")

    # Projection for Aug 9 (after the second event) must see MISSED,
    # which pushes delay probability up sharply.
    after = seeded_client.post(
        "/api/v1/orders/WMT-100234/projections",
        json={
            "projection_date": "2026-08-09",
        },
    ).json()
    delay_after = next(v for v in after["violations"] if v["violation_type"] == "OTIF_LATE")

    assert delay_after["probability"] > delay_before["probability"]


def test_actual_fines_recorded_and_listed(seeded_client):
    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/actual-fines",
        json={
            "actual_fine_id": "AF-1",
            "violation_type": "OTIF_LATE",
            "actual_fine_amount": 980.00,
            "invoice_or_deduction_date": "2026-08-21",
        },
    )
    assert resp.status_code == 201

    listed = seeded_client.get("/api/v1/orders/WMT-100234/actual-fines")
    assert listed.status_code == 200
    assert listed.json()[0]["actual_fine_amount"] == 980.00
    assert listed.json()[0]["dispute_status"] == "NONE"
