"""Integration tests for the mitigation-options endpoints and the
"orders/{order_id}/mitigation-options/runs" combined endpoint (app/api/v1/fine_mitigation/mitigations.py)."""

from __future__ import annotations

from app.api.dependencies import get_llm_client


class _FakeAIMessage:
    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls


class FakeChatClient:
    model_name = "fake-model"

    def __init__(self, summary_text: str = "Accepting the fine is currently the best option."):
        self._summary_text = summary_text

    def invoke(self, messages, *, tools=None):
        return _FakeAIMessage(content=self._summary_text, tool_calls=[])


def _override_llm_client(client, summary_text: str | None = None) -> None:
    client.app.dependency_overrides[get_llm_client] = lambda: (
        FakeChatClient(summary_text) if summary_text else FakeChatClient()
    )


def _run_projection(seeded_client, order_id: str = "WMT-100234", projection_date: str = "2026-08-09"):
    resp = seeded_client.post(
        f"/api/v1/orders/{order_id}/projections",
        json={"projection_date": projection_date},
    )
    assert resp.status_code == 201, resp.text


def test_mitigation_options_post_404s_for_unknown_order(seeded_client):
    resp = seeded_client.post("/api/v1/orders/NOPE/mitigation-options", json={})
    assert resp.status_code == 404


def test_mitigation_options_post_422s_when_no_projection_exists_yet(seeded_client):
    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-options",
        json={"projection_date": "2026-08-09"},
    )
    assert resp.status_code == 422


def test_mitigation_options_get_404s_for_unknown_order(seeded_client):
    resp = seeded_client.get("/api/v1/orders/NOPE/mitigation-options")
    assert resp.status_code == 404


def test_mitigation_options_get_422s_when_nothing_computed_yet(seeded_client):
    _run_projection(seeded_client)
    resp = seeded_client.get("/api/v1/orders/WMT-100234/mitigation-options")
    assert resp.status_code == 422


def test_mitigation_options_post_then_get_returns_ranked_options(seeded_client):
    _run_projection(seeded_client)

    post_resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-options",
        json={"projection_date": "2026-08-09"},
    )
    assert post_resp.status_code == 201, post_resp.text
    body = post_resp.json()
    assert body["order_id"] == "WMT-100234"
    assert body["projection_date"] == "2026-08-09"
    assert any(o["action"] == "ACCEPT" for o in body["options"])
    net_savings = [o["net_saving"] for o in body["options"]]
    assert net_savings == sorted(net_savings, reverse=True)

    get_resp = seeded_client.get("/api/v1/orders/WMT-100234/mitigation-options")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json() == body


def test_mitigation_run_computes_options_and_schedules_summary(seeded_client):
    _run_projection(seeded_client)
    _override_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-options/runs",
        json={"projection_date": "2026-08-09"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["mitigation_options"]["order_id"] == "WMT-100234"
    assert body["mitigation_options"]["options"]
    assert body["summary"]["status"] == "PENDING"

    status_resp = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        params={"as_of_date": "2026-08-09"},
    )
    assert status_resp.status_code == 200, status_resp.text
    assert status_resp.json()["status"] == "READY"


def test_mitigation_run_cache_hit_returns_200(seeded_client):
    _run_projection(seeded_client)
    _override_llm_client(seeded_client)

    first = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-options/runs",
        json={"projection_date": "2026-08-09"},
    )
    assert first.status_code == 202, first.text

    second = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-options/runs",
        json={"projection_date": "2026-08-09"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["summary"]["status"] == "READY"
