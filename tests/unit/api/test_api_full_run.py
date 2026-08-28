"""Integration tests for the POST /orders/{order_id}/fine-runs combined endpoint
(app/api/v1/fine_runs.py) -- projection -> projection summary ->
mitigation options -> mitigation summary, chained in one call."""

from __future__ import annotations

from app.api.dependencies import get_llm_client


class _FakeAIMessage:
    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls


class FakeChatClient:
    model_name = "fake-model"

    def __init__(self, summary_text: str = "Fine, all clear.") -> None:
        self._summary_text = summary_text

    def invoke(self, messages, *, tools=None):
        return _FakeAIMessage(content=self._summary_text, tool_calls=[])


def _override_llm_client(client) -> None:
    client.app.dependency_overrides[get_llm_client] = lambda: FakeChatClient()


def test_fine_runs_computes_all_four_pieces_and_schedules_both_summaries(seeded_client):
    _override_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/fine-runs",
        json={"projection_date": "2026-08-09"},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["projection"]["order_id"] == "WMT-100234"
    assert body["projection"]["projection_date"] == "2026-08-09"
    assert body["projection_summary"]["status"] == "PENDING"
    assert body["mitigation_options"]["order_id"] == "WMT-100234"
    assert body["mitigation_options"]["projection_date"] == "2026-08-09"
    assert body["mitigation_options"]["options"]
    assert body["mitigation_summary"]["status"] == "PENDING"

    # TestClient runs BackgroundTasks synchronously before returning, so
    # both summaries are already resolved by the time this polls them.
    projection_summary = seeded_client.get(
        "/api/v1/orders/WMT-100234/projection-summary",
        params={"as_of_date": "2026-08-09"},
    )
    assert projection_summary.json()["status"] == "READY"

    mitigation_summary = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        params={"as_of_date": "2026-08-09"},
    )
    assert mitigation_summary.json()["status"] == "READY"


def test_fine_runs_cache_hit_on_both_summaries_returns_200(seeded_client):
    _override_llm_client(seeded_client)

    first = seeded_client.post(
        "/api/v1/orders/WMT-100234/fine-runs",
        json={"projection_date": "2026-08-09"},
    )
    assert first.status_code == 202, first.text

    second = seeded_client.post(
        "/api/v1/orders/WMT-100234/fine-runs",
        json={"projection_date": "2026-08-09"},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["projection_summary"]["status"] == "READY"
    assert body["mitigation_summary"]["status"] == "READY"
    assert body["projection_summary"]["summary"] is not None
    assert body["mitigation_summary"]["summary"] is not None


def test_fine_runs_mixed_cache_state_is_202(seeded_client):
    """Projection summary pre-cached via the standalone /projections/runs endpoint,
    mitigation summary not yet generated -- fine-runs must still resolve
    both, returning 202 because at least one part was scheduled."""
    _override_llm_client(seeded_client)

    warm = seeded_client.post(
        "/api/v1/orders/WMT-100234/projections/runs",
        json={"projection_date": "2026-08-09"},
    )
    assert warm.status_code == 202, warm.text

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/fine-runs",
        json={"projection_date": "2026-08-09"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["projection_summary"]["status"] == "READY"
    assert body["mitigation_summary"]["status"] == "PENDING"

    mitigation_summary = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        params={"as_of_date": "2026-08-09"},
    )
    assert mitigation_summary.json()["status"] == "READY"


def test_fine_runs_for_unknown_order_is_404(seeded_client):
    resp = seeded_client.post("/api/v1/orders/NOPE-999/fine-runs", json={})
    assert resp.status_code == 404


def test_fine_runs_mitigation_ranks_against_the_freshly_computed_date_not_a_stale_latest(
    seeded_client,
):
    """The endpoint's core guarantee: mitigation is ranked against
    projection_result.projection_date (the date *this* call's projection
    step just computed), not against whatever projection happens to
    already be 'latest' in the database from an earlier, different call.

    Sets up a stale newer projection first (from a standalone call), then
    calls fine-runs with an older, explicit projection_date. If the wiring
    used the stale latest projection instead of the value this call just
    produced, mitigation_options.projection_date would come back as the
    newer stale date instead of the older one this call actually ran.
    """
    stale_newer_date = "2026-08-10"
    this_call_older_date = "2026-08-05"

    stale = seeded_client.post(
        "/api/v1/orders/WMT-100234/projections",
        json={"projection_date": stale_newer_date},
    )
    assert stale.status_code == 201, stale.text

    _override_llm_client(seeded_client)
    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/fine-runs",
        json={"projection_date": this_call_older_date},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["projection"]["projection_date"] == this_call_older_date
    assert body["mitigation_options"]["projection_date"] == this_call_older_date
    assert body["mitigation_summary"]["as_of_date"] == this_call_older_date
