"""API tests for `penalties.penalty_rule`."""

from __future__ import annotations

import pytest


@pytest.fixture
def retailer(client):
    return client.post(
        "/api/v1/retailers", json={"retailer_code": "RET-RULE", "retailer_name": "Rule Co"}
    ).json()["data"]


def test_create_flat_rate_rule(client, retailer):
    resp = client.post(
        "/api/v1/penalty-rules",
        json={
            "rule_code": "RULE-FLAT",
            "retailer_id": retailer["id"],
            "violation_type": "SHORT_SHIP",
            "calc_type": "PER_UNIT",
            "rate": 2.5,
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["data"]
    assert created["calc_type"] == "PER_UNIT"
    assert created["is_active"] is True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pre-existing bug in app/core/exceptions.py::_request_validation_error_handler "
        "(out of this phase's scope, file is explicitly do-not-touch): a Pydantic "
        "model_validator that raises ValueError produces an error dict with a "
        "non-JSON-serializable ctx.error object, so FastAPI's RequestValidationError "
        "-> 422 path 500s instead. Affects every custom validator in the app (not "
        "introduced here) -- only now exercised because Phase 7a restores the "
        "app/client TestClient fixtures for the first time. Flagged in the phase report."
    ),
)
def test_tiered_rule_without_tiers_is_rejected(client, retailer):
    resp = client.post(
        "/api/v1/penalty-rules",
        json={
            "rule_code": "RULE-BAD-TIERED",
            "retailer_id": retailer["id"],
            "violation_type": "SHORT_SHIP",
            "calc_type": "TIERED",
        },
    )
    assert resp.status_code == 422


def test_list_rules_filters_by_retailer(client, retailer):
    other_retailer = client.post(
        "/api/v1/retailers", json={"retailer_code": "RET-OTHER", "retailer_name": "Other"}
    ).json()["data"]

    client.post(
        "/api/v1/penalty-rules",
        json={
            "rule_code": "RULE-MINE",
            "retailer_id": retailer["id"],
            "violation_type": "OTIF_LATE",
            "calc_type": "FLAT_FEE",
            "rate": 100.0,
        },
    )
    client.post(
        "/api/v1/penalty-rules",
        json={
            "rule_code": "RULE-THEIRS",
            "retailer_id": other_retailer["id"],
            "violation_type": "OTIF_LATE",
            "calc_type": "FLAT_FEE",
            "rate": 50.0,
        },
    )

    mine = client.get("/api/v1/penalty-rules", params={"retailer_id": retailer["id"]}).json()["data"]
    assert {r["rule_code"] for r in mine} == {"RULE-MINE"}
