"""HTTP-level tests for the PO delivery-date change request/response endpoints --
full stack: router -> PoDeliveryChangeRequestService -> repositories -> SQLite test DB.

Dates are computed relative to date.today() rather than hardcoded, so the
lead-time gate (Retailer.extension_min_lead_days) never goes stale as real time
passes (the seeded 2026-08 demo orders would, since they're fixed in the past by now).
"""

from datetime import date, timedelta

_TODAY = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug (see module docstring)


def _seed_order(client, order_id: str) -> None:
    client.post("/api/v1/retailers", json={"retailer_id": "RET-EXTAPI", "retailer_name": "Ext API Retailer"})
    client.post("/api/v1/skus", json={"sku_id": "SKU-EXTAPI", "sku_code": "MAT-EXTAPI"})
    client.post("/api/v1/locations", json={"location_id": "LOC-EXTAPI", "location_type": "PLANT"})
    client.post(
        "/api/v1/fine-rules",
        json={
            "rule_id": f"RULE-{order_id}",
            "retailer_id": "RET-EXTAPI",
            "violation_type": "OTIF_LATE",
            "calc_type": "FLAT_FEE",
            "rate": 25.0,
        },
    )
    resp = client.post(
        "/api/v1/orders",
        json={
            "order_id": order_id,
            "retailer_id": "RET-EXTAPI",
            "sku_id": "SKU-EXTAPI",
            "ship_from_location_id": "LOC-EXTAPI",
            "order_qty": 1000,
            "unit_price": 10.0,
            "order_date": _TODAY.isoformat(),
            "requested_delivery_date": (_TODAY + timedelta(days=12)).isoformat(),
            "required_ship_date": (_TODAY + timedelta(days=10)).isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text


def test_create_po_delivery_change_request(client):
    _seed_order(client, "ORD-EXTAPI-1")

    resp = client.post(
        "/api/v1/orders/ORD-EXTAPI-1/po-delivery-change-requests",
        json={"reason_code": "DELAY", "proposed_delivery_date": (_TODAY + timedelta(days=16)).isoformat()},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["order_id"] == "ORD-EXTAPI-1"


def test_duplicate_active_request_returns_409(client):
    _seed_order(client, "ORD-EXTAPI-2")
    payload = {"reason_code": "SHORTAGE", "proposed_delivery_date": (_TODAY + timedelta(days=16)).isoformat()}

    first = client.post("/api/v1/orders/ORD-EXTAPI-2/po-delivery-change-requests", json=payload)
    assert first.status_code == 201, first.text

    second = client.post("/api/v1/orders/ORD-EXTAPI-2/po-delivery-change-requests", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ACTIVE_PO_DELIVERY_CHANGE_REQUEST_EXISTS"


def test_response_requires_countered_date_only_when_countered(client):
    _seed_order(client, "ORD-EXTAPI-3")
    created = client.post(
        "/api/v1/orders/ORD-EXTAPI-3/po-delivery-change-requests",
        json={"reason_code": "OTHER", "proposed_delivery_date": (_TODAY + timedelta(days=16)).isoformat()},
    )
    request_id = created.json()["request_id"]

    missing_counter_date = client.post(
        f"/api/v1/po-delivery-change-requests/{request_id}/response",
        json={"decision": "COUNTERED"},
    )
    assert missing_counter_date.status_code == 422

    unexpected_counter_date = client.post(
        f"/api/v1/po-delivery-change-requests/{request_id}/response",
        json={"decision": "ACCEPTED", "countered_delivery_date": (_TODAY + timedelta(days=14)).isoformat()},
    )
    assert unexpected_counter_date.status_code == 422


def test_full_lifecycle_accept_updates_order_and_reprojects(client):
    """Full lifecycle: create -> accept -> Order.current_delivery_date updated
    -> a fresh ProjectedFine reflects the new date (per the plan's Verification
    section). Order.current_delivery_date isn't exposed on OrderResponse (kept
    out of scope, see OrderRequest/OrderResponse in app/schemas/orders.py), so
    that half is asserted via the PO delivery-change request history/response
    body and the projection history endpoint instead.
    """
    _seed_order(client, "ORD-EXTAPI-4")
    proposed = _TODAY + timedelta(days=16)

    created = client.post(
        "/api/v1/orders/ORD-EXTAPI-4/po-delivery-change-requests",
        json={"reason_code": "DELAY", "proposed_delivery_date": proposed.isoformat()},
    )
    request_id = created.json()["request_id"]

    accepted = client.post(
        f"/api/v1/po-delivery-change-requests/{request_id}/response",
        json={"decision": "ACCEPTED"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ACCEPTED"

    history = client.get("/api/v1/orders/ORD-EXTAPI-4/po-delivery-change-requests")
    assert history.status_code == 200
    assert history.json()[0]["status"] == "ACCEPTED"

    # record_response re-triggers projection inline -- the fresh ProjectedFine
    # row must reflect the accepted (shifted) delivery date, not the original.
    projections = client.get("/api/v1/orders/ORD-EXTAPI-4/projections")
    assert projections.status_code == 200
    rows = projections.json()
    assert rows
    assert all(row["days_to_delivery"] == (proposed - _TODAY).days for row in rows)


def test_reject_leaves_projection_on_original_date(client):
    _seed_order(client, "ORD-EXTAPI-5")
    original_delivery = _TODAY + timedelta(days=12)

    created = client.post(
        "/api/v1/orders/ORD-EXTAPI-5/po-delivery-change-requests",
        json={"reason_code": "SHORTAGE", "proposed_delivery_date": (_TODAY + timedelta(days=16)).isoformat()},
    )
    request_id = created.json()["request_id"]

    rejected = client.post(
        f"/api/v1/po-delivery-change-requests/{request_id}/response",
        json={"decision": "REJECTED"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"

    projections = client.get("/api/v1/orders/ORD-EXTAPI-5/projections")
    rows = projections.json()
    assert rows
    assert all(row["days_to_delivery"] == (original_delivery - _TODAY).days for row in rows)


def test_po_delivery_change_requests_require_existing_order(client):
    resp = client.get("/api/v1/orders/does-not-exist/po-delivery-change-requests")
    assert resp.status_code == 404

    resp = client.post(
        "/api/v1/orders/does-not-exist/po-delivery-change-requests",
        json={"reason_code": "OTHER", "proposed_delivery_date": (_TODAY + timedelta(days=16)).isoformat()},
    )
    assert resp.status_code == 404
