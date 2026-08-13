"""
Runs a projection through the full HTTP -> router -> service ->
repository -> DB stack and checks it against the same baseline number
independently verified by tests/test_fine_engine.py and
docs/FINE_ENGINE.md (Aug 3 baseline row for WMT-100234: 5% shortage / $0,
5% delay / $54, total $54). If the API layer ever disagrees with the
pure-engine numbers, something broke in the plumbing, not the model.
"""

from datetime import date

from app.api.dependencies import get_llm_client
from app.models import FineRule


class _FakeAIMessage:
    def __init__(self, content: str | None, tool_calls: list[dict]) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChatClient:
    model_name = "fake-model"

    def __init__(self, summary_text: str = "Fine, all clear."):
        self._summary_text = summary_text

    def invoke(self, messages, *, tools=None):
        return _FakeAIMessage(content=self._summary_text, tool_calls=[])


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


def test_run_projection_with_corrupt_calc_type_is_500(client, db_session):
    """InvalidFineRuleDataError (a data-integrity failure in dim_fine_rule,
    not a client input error) must surface as a 500 through the real HTTP
    stack -- proven at the repository level in tests/test_repositories.py,
    this proves the router/service layers pass it through unmodified too."""
    client.post("/api/v1/retailers", json={"retailer_id": "RET-CORRUPT", "retailer_name": "Corrupt Co"})
    client.post("/api/v1/skus", json={"sku_id": "SKU-CORRUPT", "sku_code": "MAT-CORRUPT"})
    client.post("/api/v1/locations", json={"location_id": "LOC-CORRUPT", "location_type": "PLANT"})
    client.post(
        "/api/v1/orders",
        json={
            "order_id": "ORD-CORRUPT",
            "retailer_id": "RET-CORRUPT",
            "sku_id": "SKU-CORRUPT",
            "ship_from_location_id": "LOC-CORRUPT",
            "order_qty": 100,
            "unit_price": 5.0,
            "order_date": "2026-08-01",
            "requested_delivery_date": "2026-08-10",
            "required_ship_date": "2026-08-08",
        },
    )

    # calc_type this corrupt can't be introduced through the API (the
    # request schema restricts it to a Literal) -- only a bad row already
    # in the database, written directly here.
    db_session.add(
        FineRule(
            rule_id="RULE-CORRUPT",
            retailer_id="RET-CORRUPT",
            violation_type="SHORT_SHIP",
            calc_type="NOT_A_REAL_CALC_TYPE",
            rate=1.0,
            threshold_pct=0.0,
            is_active=True,
            effective_start_date=date(2026, 1, 1),
        )
    )
    db_session.commit()

    resp = client.post("/api/v1/projections/run", json={"order_id": "ORD-CORRUPT"})

    assert resp.status_code == 500, resp.text
    assert resp.json()["error"]["code"] == "INVALID_FINE_RULE_DATA"


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


def test_run_endpoint_runs_projection_then_schedules_summary(seeded_client):
    """POST /orders/{order_id}/run composes ProjectionService and
    FineSummaryService in the right order -- a summary is never scheduled
    for a day that wasn't actually just projected."""
    seeded_client.app.dependency_overrides[get_llm_client] = lambda: _FakeChatClient()

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/run",
        json={"projection_date": "2026-08-02"},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["projection"]["order_id"] == "WMT-100234"
    assert body["projection"]["projection_date"] == "2026-08-02"
    assert body["summary"]["status"] == "PENDING"

    # TestClient runs BackgroundTasks synchronously before returning, so
    # the summary is already resolved by the time this polls it.
    status = seeded_client.get(
        "/api/v1/orders/WMT-100234/summary",
        params={"as_of_date": "2026-08-02"},
    )
    assert status.json()["status"] == "READY"

    seeded_client.app.dependency_overrides.pop(get_llm_client, None)


def test_run_endpoint_for_unknown_order_is_404(seeded_client):
    resp = seeded_client.post("/api/v1/orders/NOPE-999/run", json={})
    assert resp.status_code == 404
