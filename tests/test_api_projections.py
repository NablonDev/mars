"""
Runs a projection through the full HTTP -> router -> service ->
repository -> DB stack and checks it against the same baseline number
independently verified by tests/test_fine_engine.py and
docs/FINE_ENGINE.md (Aug 3 baseline row for WMT-100234: 5% shortage / $0,
5% delay / $54, total $54). If the API layer ever disagrees with the
pure-engine numbers, something broke in the plumbing, not the model.
"""


def test_baseline_projection_matches_calibration_doc(seeded_client):
    resp = seeded_client.post(
        "/api/v1/projections/run",
        json={
            "order_id": "WMT-100234",
            "projection_date": "2026-08-02",
        },
    )
    assert resp.status_code == 200, resp.text
    [result] = resp.json()

    assert result["shortage_probability"] == 0.05
    assert result["delay_probability"] == 0.05
    assert result["total_expected_fine"] == 54.0
    assert result["stacking_mode"] == "SUM"
    violation_types = {v["violation_type"] for v in result["violations"]}
    assert violation_types == {"SHORT_SHIP", "OTIF_LATE"}


def test_projection_history_and_exposure_after_run(seeded_client):
    seeded_client.post(
        "/api/v1/projections/run",
        json={
            "order_id": "WMT-100234",
            "projection_date": "2026-08-02",
        },
    )

    history = seeded_client.get("/api/v1/orders/WMT-100234/projections")
    assert history.status_code == 200
    assert len(history.json()) == 2  # SHORT_SHIP + OTIF_LATE rows

    exposure = seeded_client.get("/api/v1/orders/WMT-100234/exposure")
    assert exposure.status_code == 200
    assert exposure.json()["total_expected_fine"] == 54.0


def test_rerunning_same_order_date_updates_not_duplicates(seeded_client):
    seeded_client.post(
        "/api/v1/projections/run",
        json={
            "order_id": "WMT-100234",
            "projection_date": "2026-08-02",
        },
    )
    seeded_client.post(
        "/api/v1/projections/run",
        json={
            "order_id": "WMT-100234",
            "projection_date": "2026-08-02",
        },
    )
    history = seeded_client.get("/api/v1/orders/WMT-100234/projections")
    assert len(history.json()) == 2  # still 2, not 4 -- upsert, not insert


def test_run_projection_for_unknown_order_is_404(seeded_client):
    resp = seeded_client.post("/api/v1/projections/run", json={"order_id": "NOPE-999"})
    assert resp.status_code == 404


def test_run_projection_without_order_id_or_all_open_is_422(seeded_client):
    resp = seeded_client.post("/api/v1/projections/run", json={})
    assert resp.status_code == 422


def test_run_projection_for_retailer_with_no_rules_is_422(client):
    client.post("/api/v1/retailers", json={"retailer_id": "RET-NORULES", "retailer_name": "No Rules Inc"})
    client.post("/api/v1/skus", json={"sku_id": "SKU-X", "sku_code": "MAT-X"})
    client.post("/api/v1/locations", json={"location_id": "LOC-X", "location_type": "PLANT"})
    client.post(
        "/api/v1/orders",
        json={
            "order_id": "ORD-NORULES",
            "retailer_id": "RET-NORULES",
            "sku_id": "SKU-X",
            "ship_from_location_id": "LOC-X",
            "order_qty": 100,
            "unit_price": 5.0,
            "order_date": "2026-08-01",
            "requested_delivery_date": "2026-08-10",
            "required_ship_date": "2026-08-08",
        },
    )

    resp = client.post("/api/v1/projections/run", json={"order_id": "ORD-NORULES"})
    assert resp.status_code == 422


def test_run_all_open_projects_every_open_order(seeded_client):
    resp = seeded_client.post(
        "/api/v1/projections/run",
        json={
            "all_open": True,
            "projection_date": "2026-08-02",
        },
    )
    assert resp.status_code == 200
    order_ids = {r["order_id"] for r in resp.json()}
    assert order_ids == {"WMT-100234", "WMT-100511", "AMZ-778501", "AMZ-780112"}
