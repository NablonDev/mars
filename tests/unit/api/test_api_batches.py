"""API tests for the generic `process.job_run`/`job_item` batch trigger/status/items
endpoints (`/job-runs`, `/job-runs/{id}`, `/job-runs/{id}/items`) -- this pass only
exercises `job_type=PENALTY_PROJECTION_BATCH` (mapped internally to
`JobTaskType.ORDER_RUN`); the CMIR follow-up pass adds coverage for its own job types."""

from __future__ import annotations

import pytest


@pytest.fixture
def retailer(client):
    return client.post(
        "/api/v1/retailers", json={"retailer_code": "RET-BATCH", "retailer_name": "Batch Co"}
    ).json()["data"]


@pytest.fixture
def open_purchase_order(client, retailer):
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "purchase_order_number": "PO-BATCH-001",
            "retailer_id": retailer["id"],
            "order_date": "2026-08-01",
            "requested_delivery_date": "2026-08-10",
            "required_ship_date": "2026-08-08",
            "lines": [{"line_number": "10", "ordered_quantity": 100, "unit_price": 5.0}],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_trigger_job_run_with_no_open_orders_returns_zero_items(client):
    resp = client.post("/api/v1/job-runs", json={})

    assert resp.status_code == 202, resp.text
    body = resp.json()["data"]
    assert body["requested_item_count"] == 0
    assert body["execution_note"]


def test_trigger_job_run_enqueues_one_item_per_open_order(client, open_purchase_order):
    resp = client.post("/api/v1/job-runs", json={})

    assert resp.status_code == 202, resp.text
    body = resp.json()["data"]
    assert body["requested_item_count"] == 1
    assert body["job_run_id"]
    assert body["dispatch_mode"]


def test_get_job_run_status_reports_counts_and_completeness(client, open_purchase_order):
    job_run_id = client.post("/api/v1/job-runs", json={}).json()["data"]["job_run_id"]

    resp = client.get(f"/api/v1/job-runs/{job_run_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["job_run_id"] == job_run_id
    assert body["requested_item_count"] == 1
    assert body["total_items"] == 1
    assert isinstance(body["is_complete"], bool)
    assert set(body["counts"].keys()) >= {"PENDING", "RUNNING", "SUCCEEDED", "DEAD"}


def test_get_job_run_status_for_unknown_run_returns_404(client):
    resp = client.get("/api/v1/job-runs/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "JOB_RUN_NOT_FOUND"


def test_list_job_run_items_returns_the_enqueued_item(client, open_purchase_order):
    job_run_id = client.post("/api/v1/job-runs", json={}).json()["data"]["job_run_id"]

    resp = client.get(f"/api/v1/job-runs/{job_run_id}/items")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["job_run_id"] == job_run_id
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["item_type"] == "ORDER_RUN"
    assert item["attempt_count"] >= 0


def test_list_job_run_items_supports_status_filter_and_pagination(client, open_purchase_order):
    job_run_id = client.post("/api/v1/job-runs", json={}).json()["data"]["job_run_id"]

    resp = client.get(f"/api/v1/job-runs/{job_run_id}/items", params={"status": "DEAD", "limit": 5})

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["items"] == []
