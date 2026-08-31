"""API tests for `common`-schema master data: retailers, retailer-owned
locations, SKUs, materials/material-masters, plants, and carriers."""

from __future__ import annotations


def test_create_and_list_retailer(client):
    resp = client.post(
        "/api/v1/retailers",
        json={"retailer_code": "RET-TEST", "retailer_name": "Test Retailer"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["success"] is True
    created = body["data"]
    assert created["retailer_code"] == "RET-TEST"
    assert created["stacking_mode"] == "SUM"
    assert "id" in created

    listed = client.get("/api/v1/retailers").json()["data"]
    assert any(r["retailer_code"] == "RET-TEST" for r in listed)


def test_create_and_list_retailer_location(client):
    retailer = client.post(
        "/api/v1/retailers", json={"retailer_code": "RET-LOC", "retailer_name": "Loc Co"}
    ).json()["data"]

    resp = client.post(
        f"/api/v1/retailers/{retailer['id']}/locations",
        json={"location_code": "DC-01", "location_name": "Main DC"},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["data"]
    assert created["retailer_id"] == retailer["id"]

    listed = client.get(f"/api/v1/retailers/{retailer['id']}/locations").json()["data"]
    assert len(listed) == 1
    assert listed[0]["location_code"] == "DC-01"


def test_create_and_list_sku(client):
    resp = client.post("/api/v1/skus", json={"sku_code": "SKU-TEST", "description": "Test SKU"})
    assert resp.status_code == 201, resp.text

    listed = client.get("/api/v1/skus").json()["data"]
    assert any(s["sku_code"] == "SKU-TEST" for s in listed)


def test_create_and_list_material(client):
    resp = client.post(
        "/api/v1/materials", json={"material_code": "MAT-TEST", "description": "Test Material"}
    )
    assert resp.status_code == 201, resp.text

    listed = client.get("/api/v1/materials").json()["data"]
    assert any(m["material_code"] == "MAT-TEST" for m in listed)


def test_create_and_list_material_master(client):
    material = client.post("/api/v1/materials", json={"material_code": "MAT-MM"}).json()["data"]
    plant = client.post("/api/v1/plants", json={"plant_code": "PLANT-MM"}).json()["data"]

    resp = client.post(
        "/api/v1/material-masters",
        json={
            "material_id": material["id"],
            "sap_material_number": "SAP-001",
            "plant_id": plant["id"],
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["data"]
    assert created["material_id"] == material["id"]

    listed = client.get("/api/v1/material-masters").json()["data"]
    assert any(mm["sap_material_number"] == "SAP-001" for mm in listed)


def test_create_and_list_plant(client):
    resp = client.post("/api/v1/plants", json={"plant_code": "PLANT-TEST", "plant_name": "Test Plant"})
    assert resp.status_code == 201, resp.text

    listed = client.get("/api/v1/plants").json()["data"]
    assert any(p["plant_code"] == "PLANT-TEST" for p in listed)


def test_create_list_and_get_carrier(client):
    resp = client.post("/api/v1/carriers", json={"carrier_code": "CAR-TEST", "carrier_name": "Test Carrier"})
    assert resp.status_code == 201, resp.text
    created = resp.json()["data"]

    listed = client.get("/api/v1/carriers").json()["data"]
    assert any(c["carrier_code"] == "CAR-TEST" for c in listed)

    fetched = client.get(f"/api/v1/carriers/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["carrier_code"] == "CAR-TEST"


def test_get_unknown_carrier_returns_404_envelope(client):
    resp = client.get("/api/v1/carriers/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "CARRIER_NOT_FOUND"
