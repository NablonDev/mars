"""
Integration test for POST /orders/{order_id}/explanation through the
full FastAPI app + TestClient. `get_llm_client` is overridden via
`app.dependency_overrides` with a hand-written fake -- this never
reaches, or even constructs a client for, real Azure OpenAI, even
through the HTTP layer.

Reuses the WMT-100234 seeded scenario from test_api_admin_seed.py for a
realistic happy-path fixture.
"""

from datetime import date

from app.agents.base import ChatTurnResult
from app.agents.explanation_schema import ProjectionExplanationOutput
from app.api.dependencies import get_llm_client


class FakeStructuredChatClient:
    """Never calls out to any tool -- returns the final structured answer
    on the first round every time."""

    model_name = "fake-model"

    def __init__(self, output: ProjectionExplanationOutput):
        self._output = output

    def call_with_tools(self, system_prompt, messages, tools):
        return ChatTurnResult(tool_calls=[], content=None)

    def call_structured(self, system_prompt, messages, output_schema):
        return self._output


def _fake_output(order_id: str) -> ProjectionExplanationOutput:
    return ProjectionExplanationOutput(
        order_id=order_id,
        as_of_date=date(2026, 8, 9),
        headline_summary="Carrier missed the dock appointment on Aug 9, raising delay risk to 50%.",
        current_total_expected_fine=540.0,
        stacking_mode="SUM",
        violations=[],
        day_by_day_narrative=[],
        key_sensitivity_factors=["carrier appointment status", "production status"],
        caveats=[],
    )


def _override_llm_client(client, output: ProjectionExplanationOutput) -> None:
    client.app.dependency_overrides[get_llm_client] = lambda: FakeStructuredChatClient(output)


def test_explanation_404s_for_unknown_order(seeded_client):
    _override_llm_client(seeded_client, _fake_output("NOPE"))

    resp = seeded_client.post("/api/v1/orders/NOPE/explanation", json={})

    assert resp.status_code == 404


def test_explanation_422s_when_no_projection_exists_yet(seeded_client):
    """WMT-100234 is seeded but has never had a projection run for it in
    this test (unlike test_simulate_daily_run, which this test
    deliberately doesn't call) -- so there's nothing to explain yet."""
    _override_llm_client(seeded_client, _fake_output("WMT-100234"))

    resp = seeded_client.post("/api/v1/orders/WMT-100234/explanation", json={})

    assert resp.status_code == 422


def test_explanation_200s_for_a_real_projected_order(seeded_client):
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    _override_llm_client(seeded_client, _fake_output("WMT-100234"))

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/explanation",
        json={"as_of_date": "2026-08-09"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["order_id"] == "WMT-100234"
    assert body["current_total_expected_fine"] == 540.0
    assert body["stacking_mode"] == "SUM"
    assert body["violations"] == []
    assert body["caveats"] == []

    # Second call with the same as_of_date is a cache hit -- swap the
    # override for a client that would raise if it were actually called,
    # to prove the LLM isn't invoked again.
    def _explode(*args, **kwargs):
        raise AssertionError("LLM should not be called on a cache hit")

    class ExplodingClient:
        model_name = "fake-model"
        call_with_tools = staticmethod(_explode)
        call_structured = staticmethod(_explode)

    seeded_client.app.dependency_overrides[get_llm_client] = lambda: ExplodingClient()

    cached_resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/explanation",
        json={"as_of_date": "2026-08-09"},
    )
    assert cached_resp.status_code == 200
    assert cached_resp.json() == body


def test_explanation_422s_for_an_as_of_date_in_the_future(seeded_client):
    """Regression test for the unbounded-cache-key abuse vector: without
    server-side validation, a client could vary as_of_date arbitrarily
    to mint fresh cache keys and trigger unlimited real LLM calls. The
    fake client here would raise if it were ever actually invoked."""
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    def _explode(*args, **kwargs):
        raise AssertionError("LLM should not be called for an out-of-range as_of_date")

    class ExplodingClient:
        model_name = "fake-model"
        call_with_tools = staticmethod(_explode)
        call_structured = staticmethod(_explode)

    seeded_client.app.dependency_overrides[get_llm_client] = lambda: ExplodingClient()

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/explanation",
        json={"as_of_date": "2099-01-01"},
    )

    assert resp.status_code == 422, resp.text


def test_explanation_422s_for_an_as_of_date_before_the_earliest_projection(seeded_client):
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    def _explode(*args, **kwargs):
        raise AssertionError("LLM should not be called for an out-of-range as_of_date")

    class ExplodingClient:
        model_name = "fake-model"
        call_with_tools = staticmethod(_explode)
        call_structured = staticmethod(_explode)

    seeded_client.app.dependency_overrides[get_llm_client] = lambda: ExplodingClient()

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/explanation",
        json={"as_of_date": "2000-01-01"},
    )

    assert resp.status_code == 422, resp.text


def test_explanation_force_regenerate_against_an_existing_cached_row_succeeds(seeded_client):
    """End-to-end regression test for the force_regenerate=True fix: it
    used to 500 (unhandled IntegrityError) the second time it was called
    against the same (order_id, as_of_date) -- because
    ExplanationRepository.save is insert-only. It must now succeed and
    return the freshly regenerated content, not the stale cached one."""
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    _override_llm_client(seeded_client, _fake_output("WMT-100234"))
    first_resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/explanation",
        json={"as_of_date": "2026-08-09"},
    )
    assert first_resp.status_code == 200, first_resp.text

    regenerated = _fake_output("WMT-100234")
    regenerated.headline_summary = "Regenerated: new carrier reliability data lowers delay risk."
    _override_llm_client(seeded_client, regenerated)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/explanation",
        json={"as_of_date": "2026-08-09", "force_regenerate": True},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["headline_summary"] == regenerated.headline_summary
    assert resp.json()["headline_summary"] != first_resp.json()["headline_summary"]
