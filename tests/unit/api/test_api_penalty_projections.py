"""API tests for `penalties.penalty_projection`: running/history/exposure,
the cross-PO open-projection list, single-projection reads, and the
projection-summary trigger/poll contract.

Uses `seeded_client` (the four worked-example purchase orders, replayed
day by day) rather than building fixtures from scratch -- every purchase
order already has real projection history and, for one of them, a PO
delivery-change-request negotiation outcome.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def wmt_purchase_order(seeded_client) -> dict:
    purchase_orders = seeded_client.get("/api/v1/purchase-orders").json()["data"]
    return next(po for po in purchase_orders if po["purchase_order_number"] == "WMT-100234")


def test_get_penalty_projection_history(seeded_client, wmt_purchase_order):
    resp = seeded_client.get(f"/api/v1/purchase-orders/{wmt_purchase_order['id']}/penalty-projections")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) > 0
    assert all(row["purchase_order_id"] == wmt_purchase_order["id"] for row in rows)
    assert all(row["summary_status"] is None for row in rows)


def test_penalty_projection_history_row_exposes_raw_amount_separately_from_combined(
    seeded_client, wmt_purchase_order
):
    """`penalty_amount` (raw $, pre-multiplication) must always
    be present on a persisted row, distinct from `expected_penalty_amount`
    (probability-weighted). Together with `failure_probability` they are the
    two decomposed components of the combined figure -- neither the manager's
    complaint nor the API contract is satisfied by the blended number alone.
    """
    resp = seeded_client.get(f"/api/v1/purchase-orders/{wmt_purchase_order['id']}/penalty-projections")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) > 0
    for row in rows:
        assert "penalty_amount" in row
        assert isinstance(row["penalty_amount"], float)
        assert row["penalty_amount"] >= 0.0
        # The raw amount is the pre-multiplication figure -- it should never
        # be smaller than the already-probability-weighted combined amount
        # (probability is always <= 1).
        assert row["penalty_amount"] >= row["expected_penalty_amount"] - 0.01


def test_get_penalty_exposure(seeded_client, wmt_purchase_order):
    resp = seeded_client.get(f"/api/v1/purchase-orders/{wmt_purchase_order['id']}/penalty-exposure")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["purchase_order_id"] == wmt_purchase_order["id"]
    assert "total_expected_penalty_amount" in body
    assert len(body["violations"]) > 0
    for violation in body["violations"]:
        assert "penalty_amount" in violation


def test_penalty_exposure_respects_max_stacking_mode(client):
    """Regression test for a real bug: `PenaltyProjectionRepository.
    get_latest` used to hardcode `sum()` over every violation's
    `expected_penalty_amount` regardless of the retailer's actual
    `stacking_mode`, unlike `ProjectionEngine.project`/`MitigationService.
    _build_projection_result`, which both correctly branch SUM/MAX. Two
    SHORTAGE-model rules (`SHORT_SHIP`/`FILL_RATE`) share the same
    probability but price very differently (FLAT_FEE 100 vs. 900), so
    SUM and MAX give clearly different totals -- a MAX retailer's exposure
    must equal the larger single violation, not the sum of both.
    """
    retailer = client.post(
        "/api/v1/retailers",
        json={"retailer_code": "RET-MAX", "retailer_name": "Max Stacking Co", "stacking_mode": "MAX"},
    ).json()["data"]
    assert retailer["stacking_mode"] == "MAX"

    client.post(
        "/api/v1/penalty-rules",
        json={
            "rule_code": "RULE-MAX-LOW",
            "retailer_id": retailer["id"],
            "violation_type": "SHORT_SHIP",
            "calc_type": "FLAT_FEE",
            "rate": 100.0,
        },
    )
    client.post(
        "/api/v1/penalty-rules",
        json={
            "rule_code": "RULE-MAX-HIGH",
            "retailer_id": retailer["id"],
            "violation_type": "FILL_RATE",
            "calc_type": "FLAT_FEE",
            "rate": 900.0,
        },
    )
    po = client.post(
        "/api/v1/purchase-orders",
        json={
            "purchase_order_number": "PO-MAX-STACK-001",
            "retailer_id": retailer["id"],
            "order_date": "2026-01-01",
            "requested_delivery_date": "2026-01-10",
            "required_ship_date": "2026-01-08",
            "lines": [{"line_number": "10", "ordered_quantity": 100, "unit_price": 5.0}],
        },
    ).json()["data"]
    # No confirmation posted -- the full order_qty is an unconfirmed
    # shortfall, so both FLAT_FEE rules price as nonzero and share the same
    # (nonzero) shortage probability.
    run_resp = client.post(f"/api/v1/purchase-orders/{po['id']}/penalty-projections", json={})
    assert run_resp.status_code == 201, run_resp.text
    violations = run_resp.json()["data"]["violations"]
    assert len(violations) == 2
    expected_by_type = {v["violation_type"]: v["expected_penalty_amount"] for v in violations}
    expected_sum = round(sum(expected_by_type.values()), 2)
    expected_max = round(max(expected_by_type.values()), 2)
    assert expected_max < expected_sum, "test setup needs two distinctly-priced violations"

    resp = client.get(f"/api/v1/purchase-orders/{po['id']}/penalty-exposure")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total_expected_penalty_amount"] == expected_max
    assert body["total_expected_penalty_amount"] != expected_sum


def test_get_penalty_exposure_for_purchase_order_without_projections_returns_404(client):
    retailer = client.post(
        "/api/v1/retailers", json={"retailer_code": "RET-EXP", "retailer_name": "Exposure Co"}
    ).json()["data"]
    po = client.post(
        "/api/v1/purchase-orders",
        json={
            "purchase_order_number": "PO-NO-PROJECTION",
            "retailer_id": retailer["id"],
            "order_date": "2026-08-01",
            "lines": [{"line_number": "10", "ordered_quantity": 10, "unit_price": 1.0}],
        },
    ).json()["data"]

    resp = client.get(f"/api/v1/purchase-orders/{po['id']}/penalty-exposure")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NO_PROJECTION_EXISTS"


def test_get_single_projection_by_id(seeded_client, wmt_purchase_order):
    history = seeded_client.get(
        f"/api/v1/purchase-orders/{wmt_purchase_order['id']}/penalty-projections"
    ).json()["data"]
    projection_id = history[0]["id"]

    resp = seeded_client.get(f"/api/v1/penalty-projections/{projection_id}")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["id"] == projection_id
    assert body["purchase_order_id"] == wmt_purchase_order["id"]
    assert body["summary_status"] is None
    assert body["summary"] is None


def test_get_single_projection_with_include_summary_is_still_a_pure_read(seeded_client, wmt_purchase_order):
    """No summary has ever been requested for this PO -- `include=summary`
    must not schedule one (approved plan §5's "?include= is pure read,
    never schedules" rule)."""
    history = seeded_client.get(
        f"/api/v1/purchase-orders/{wmt_purchase_order['id']}/penalty-projections"
    ).json()["data"]
    projection_id = history[0]["id"]

    resp = seeded_client.get(f"/api/v1/penalty-projections/{projection_id}", params={"include": "summary"})

    assert resp.status_code == 200
    assert resp.json()["data"]["summary_status"] is None
    assert resp.json()["data"]["summary"] is None


def test_get_projection_rejects_an_unknown_include_value(seeded_client, wmt_purchase_order):
    history = seeded_client.get(
        f"/api/v1/purchase-orders/{wmt_purchase_order['id']}/penalty-projections"
    ).json()["data"]
    projection_id = history[0]["id"]

    resp = seeded_client.get(f"/api/v1/penalty-projections/{projection_id}", params={"include": "bogus"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_INCLUDE"


def test_get_unknown_projection_returns_404(seeded_client):
    resp = seeded_client.get("/api/v1/penalty-projections/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PROJECTION_NOT_FOUND"


def test_list_open_penalty_projections_is_flat_and_cross_po(seeded_client, wmt_purchase_order):
    resp = seeded_client.get("/api/v1/penalty-projections", params={"status": "open"})

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) > 0
    assert all(row["projection_status"] == "OPEN" for row in rows)
    assert any(row["purchase_order_id"] == wmt_purchase_order["id"] for row in rows)


def test_run_penalty_projection_for_a_purchase_order(seeded_client, wmt_purchase_order):
    history_before = seeded_client.get(
        f"/api/v1/purchase-orders/{wmt_purchase_order['id']}/penalty-projections"
    ).json()["data"]
    an_existing_date = history_before[0]["projection_date"]

    resp = seeded_client.post(
        f"/api/v1/purchase-orders/{wmt_purchase_order['id']}/penalty-projections",
        json={"projection_date": an_existing_date},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert body["purchase_order_id"] == wmt_purchase_order["id"]
    assert body["projection_date"] == an_existing_date
    assert "total_expected_penalty_amount" in body


def test_trigger_penalty_projection_summary_queues_a_job(client):
    """Built fresh (not from `seeded_client`): every worked-example
    scenario's projection dates are forward-looking by design (order_date
    is anchored at real "today", so every tracked day is in the future
    relative to it -- see `app.services.seeding.projection`'s module
    docstring), and `ProjectionSummaryService._validate` rejects a future
    `as_of_date`. This purchase order's projection is run for real "today"
    instead, so its `as_of_date` is always valid."""
    retailer = client.post(
        "/api/v1/retailers", json={"retailer_code": "RET-SUM", "retailer_name": "Summary Co"}
    ).json()["data"]
    client.post(
        "/api/v1/penalty-rules",
        json={
            "rule_code": "RULE-SUM",
            "retailer_id": retailer["id"],
            "violation_type": "SHORT_SHIP",
            "calc_type": "PER_UNIT",
            "rate": 1.0,
        },
    )
    po = client.post(
        "/api/v1/purchase-orders",
        json={
            "purchase_order_number": "PO-SUMMARY-001",
            "retailer_id": retailer["id"],
            "order_date": "2026-01-01",
            "requested_delivery_date": "2026-01-10",
            "required_ship_date": "2026-01-08",
            "lines": [{"line_number": "10", "ordered_quantity": 100, "unit_price": 5.0}],
        },
    ).json()["data"]
    client.post(f"/api/v1/purchase-orders/{po['id']}/penalty-projections", json={})
    history = client.get(f"/api/v1/purchase-orders/{po['id']}/penalty-projections").json()["data"]
    as_of_date = history[0]["projection_date"]

    resp = client.post(
        f"/api/v1/purchase-orders/{po['id']}/penalty-projections/summary",
        json={"as_of_date": as_of_date},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()["data"]
    assert body["status"] == "PENDING"
    assert body["summary"] is None

    # `include=summary` now sees the PENDING job -- still a pure read.
    projection_id = history[0]["id"]
    detail = client.get(f"/api/v1/penalty-projections/{projection_id}", params={"include": "summary"}).json()[
        "data"
    ]
    assert detail["summary_status"] == "PENDING"
