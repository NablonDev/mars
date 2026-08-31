"""API tests for `penalties.mitigation_option`: compute/list ranked
mitigation options for a projection, and the mitigation-summary
trigger/poll contract."""

from __future__ import annotations

import pytest


@pytest.fixture
def projected_purchase_order(client) -> dict:
    """A fresh PO with one persisted OPEN penalty projection, anchored on
    real "today" (see test_api_penalty_projections.py's summary-trigger
    test for why the seeded scenario's forward-looking dates don't work
    for this)."""
    retailer = client.post(
        "/api/v1/retailers", json={"retailer_code": "RET-MIT", "retailer_name": "Mitigation Co"}
    ).json()["data"]
    client.post(
        "/api/v1/penalty-rules",
        json={
            "rule_code": "RULE-MIT",
            "retailer_id": retailer["id"],
            "violation_type": "SHORT_SHIP",
            "calc_type": "PER_UNIT",
            "rate": 1.0,
        },
    )
    po = client.post(
        "/api/v1/purchase-orders",
        json={
            "purchase_order_number": "PO-MITIGATION-001",
            "retailer_id": retailer["id"],
            "order_date": "2026-01-01",
            "requested_delivery_date": "2026-01-10",
            "required_ship_date": "2026-01-08",
            "lines": [{"line_number": "10", "ordered_quantity": 100, "unit_price": 5.0}],
        },
    ).json()["data"]
    client.post(f"/api/v1/purchase-orders/{po['id']}/penalty-projections", json={})
    return po


@pytest.fixture
def projection_id(client, projected_purchase_order) -> str:
    history = client.get(
        f"/api/v1/purchase-orders/{projected_purchase_order['id']}/penalty-projections"
    ).json()["data"]
    return history[0]["id"]


def test_run_and_list_penalty_mitigations(client, projection_id):
    resp = client.post("/api/v1/penalty-mitigations", params={"projection_id": projection_id})

    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert len(body["options"]) > 0

    listed = client.get("/api/v1/penalty-mitigations", params={"projection_id": projection_id}).json()["data"]
    assert len(listed["options"]) == len(body["options"])


def test_list_penalty_mitigations_against_unknown_projection_returns_404(client):
    resp = client.get(
        "/api/v1/penalty-mitigations",
        params={"projection_id": "00000000-0000-0000-0000-000000000000"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PROJECTION_NOT_FOUND"


def test_get_single_mitigation_by_id(client, projection_id):
    client.post("/api/v1/penalty-mitigations", params={"projection_id": projection_id})
    options = client.get("/api/v1/penalty-mitigations", params={"projection_id": projection_id}).json()[
        "data"
    ]["options"]
    mitigation_id = options[0]["id"]

    resp = client.get(f"/api/v1/penalty-mitigations/{mitigation_id}")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["id"] == mitigation_id
    assert body["summary_status"] is None


def test_get_unknown_mitigation_returns_404(client):
    resp = client.get("/api/v1/penalty-mitigations/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "MITIGATION_OPTION_NOT_FOUND"


def test_trigger_penalty_mitigation_summary_queues_a_job(client, projected_purchase_order, projection_id):
    client.post("/api/v1/penalty-mitigations", params={"projection_id": projection_id})
    history = client.get(
        f"/api/v1/purchase-orders/{projected_purchase_order['id']}/penalty-projections"
    ).json()["data"]
    as_of_date = history[0]["projection_date"]

    resp = client.post(
        f"/api/v1/purchase-orders/{projected_purchase_order['id']}/penalty-mitigations/summary",
        json={"as_of_date": as_of_date},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()["data"]
    assert body["status"] == "PENDING"
