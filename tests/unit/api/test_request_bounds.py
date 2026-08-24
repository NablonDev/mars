"""
Two API-level acceptance paths that are easy to break from the schema layer:
a 100% cut confirmation, and a TIERED fine rule whose bands are well formed.
Both go through the route (not the service directly), so a request-model
regression shows up here as a non-2xx.
"""

from __future__ import annotations


def test_a_full_cut_confirmation_is_still_accepted(seeded_client):
    """confirmed_qty=0 is a real 100% shortage, not bad input -- it must be
    accepted rather than treated as a missing or invalid quantity."""
    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/confirmations",
        json={
            "confirmation_id": "CONF-FULL-CUT",
            "confirmed_qty": 0,
            "confirmation_date": "2026-08-05T00:00:00",
        },
    )
    assert resp.status_code in (200, 201), resp.text


def test_an_ordered_tier_band_is_still_accepted(client):
    client.post("/api/v1/retailers", json={"retailer_id": "RET-A", "retailer_name": "A"})
    resp = client.post(
        "/api/v1/fine-rules",
        json={
            "rule_id": "RULE-TIERS-OK",
            "retailer_id": "RET-A",
            "violation_type": "SHORT_SHIP",
            "calc_type": "TIERED",
            "rate": 1.0,
            "tiers": [
                {"band_min": 0.0, "band_max": 0.10, "rate": 0.02},
                {"band_min": 0.10, "band_max": 1.01, "rate": 0.08},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
