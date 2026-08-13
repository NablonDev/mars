"""Integration tests for the fine-summary endpoints; TestClient runs BackgroundTasks synchronously before returning, so a PENDING job is already resolved by the time a later GET polls it."""

from app.agents.prompts.fine_summary.v2 import PROMPT_VERSION
from app.api.dependencies import get_llm_client


class _FakeAIMessage:
    def __init__(self, content: str | None, tool_calls: list[dict]) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeChatClient:
    model_name = "fake-model"

    def __init__(self, summary_text: str):
        self._summary_text = summary_text

    def invoke(self, messages, *, tools=None):
        return _FakeAIMessage(content=self._summary_text, tool_calls=[])


class FailingChatClient:
    """Simulates a provider failure; _LEAKY_DETAIL must never reach a response body."""

    model_name = "fake-model"

    def invoke(self, messages, *, tools=None):
        raise RuntimeError(_LEAKY_DETAIL)


_LEAKY_DETAIL = "simulated upstream failure: secret vendor detail XYZ123"

_SAMPLE_SUMMARY_TEXT = (
    "Carrier missed the dock appointment on Aug 9, raising delay risk to 50%, "
    "so the current total expected fine is $540.00."
)


def _override_llm_client(client, summary_text: str = _SAMPLE_SUMMARY_TEXT) -> None:
    client.app.dependency_overrides[get_llm_client] = lambda: FakeChatClient(summary_text)


def _exploding_llm_client(client) -> None:
    def _explode(*args, **kwargs):
        raise AssertionError("LLM should not be called for this request")

    class ExplodingClient:
        model_name = "fake-model"
        invoke = staticmethod(_explode)

    client.app.dependency_overrides[get_llm_client] = lambda: ExplodingClient()


def test_fine_summary_post_404s_for_unknown_order(seeded_client):
    _override_llm_client(seeded_client)

    resp = seeded_client.post("/api/v1/orders/NOPE/summary", json={})

    assert resp.status_code == 404


def test_fine_summary_post_422s_when_no_projection_exists_yet(seeded_client):
    """WMT-100234 is seeded but has no projection run in this test."""
    _override_llm_client(seeded_client)

    resp = seeded_client.post("/api/v1/orders/WMT-100234/summary", json={})

    assert resp.status_code == 422


def test_fine_summary_get_404s_for_unknown_order(seeded_client):
    resp = seeded_client.get("/api/v1/orders/NOPE/summary")

    assert resp.status_code == 404


def test_fine_summary_get_422s_when_no_projection_exists_yet(seeded_client):
    resp = seeded_client.get("/api/v1/orders/WMT-100234/summary")

    assert resp.status_code == 422


def test_fine_summary_get_404s_when_no_job_was_ever_scheduled(seeded_client):
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    resp = seeded_client.get("/api/v1/orders/WMT-100234/summary", params={"as_of_date": "2026-08-09"})

    assert resp.status_code == 404


def test_fine_summary_202s_then_ready_on_poll_for_a_real_projected_order(seeded_client):
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    _override_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/summary",
        json={"as_of_date": "2026-08-09"},
    )

    # Fast: no 45-90s+ real Azure OpenAI latency held open on this
    # connection -- a PENDING job descriptor, not the summary itself.
    assert resp.status_code == 202, resp.text
    assert resp.json() == {
        "order_id": "WMT-100234",
        "as_of_date": "2026-08-09",
        "prompt_version": PROMPT_VERSION,
        "status": "PENDING",
        "summary": None,
        "error_message": None,
    }

    status_resp = seeded_client.get("/api/v1/orders/WMT-100234/summary", params={"as_of_date": "2026-08-09"})
    assert status_resp.status_code == 200, status_resp.text
    body = status_resp.json()
    assert body["status"] == "READY"
    assert body["error_message"] is None
    summary = body["summary"]
    assert summary["order_id"] == "WMT-100234"
    assert summary["as_of_date"] == "2026-08-09"
    assert summary["prompt_version"] == PROMPT_VERSION
    assert summary["model_name"] == "fake-model"
    assert summary["summary"] == _SAMPLE_SUMMARY_TEXT

    # A second POST with the same as_of_date is a cache hit -- swap in a
    # client that raises if called, to prove the LLM isn't invoked again.
    _exploding_llm_client(seeded_client)

    cached_resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert cached_resp.status_code == 200
    assert cached_resp.json() == summary


def test_fine_summary_422s_for_an_as_of_date_in_the_future(seeded_client):
    """Regression test for the unbounded-cache-key abuse vector: the fake
    client here would raise if it were ever actually invoked -- this
    validation must happen before anything is scheduled."""
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    _exploding_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/summary",
        json={"as_of_date": "2099-01-01"},
    )

    assert resp.status_code == 422, resp.text


def test_fine_summary_422s_for_an_as_of_date_before_the_earliest_projection(seeded_client):
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    _exploding_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/summary",
        json={"as_of_date": "2000-01-01"},
    )

    assert resp.status_code == 422, resp.text


def test_fine_summary_force_regenerate_against_an_existing_ready_row_succeeds(seeded_client):
    """force_regenerate=True against an already-READY row re-arms it to
    PENDING and replaces it with freshly regenerated content, not the
    stale cached summary -- and raises no IntegrityError."""
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    _override_llm_client(seeded_client)
    first_resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert first_resp.status_code == 202, first_resp.text
    first_status = seeded_client.get("/api/v1/orders/WMT-100234/summary", params={"as_of_date": "2026-08-09"})
    assert first_status.json()["status"] == "READY"

    regenerated_text = "Regenerated: new carrier reliability data lowers delay risk."
    _override_llm_client(seeded_client, regenerated_text)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/summary",
        json={"as_of_date": "2026-08-09", "force_regenerate": True},
    )
    assert resp.status_code == 202, resp.text

    final_status = seeded_client.get("/api/v1/orders/WMT-100234/summary", params={"as_of_date": "2026-08-09"})
    assert final_status.status_code == 200
    body = final_status.json()
    assert body["status"] == "READY"
    assert body["summary"]["summary"] == regenerated_text
    assert body["summary"]["summary"] != first_status.json()["summary"]["summary"]


def test_fine_summary_background_job_failure_surfaces_as_failed_status_not_a_raw_500(seeded_client):
    """The upstream failure's raw text must never reach the response --
    only the generic client-safe FAILED message, same message/detail
    split AppError enforces everywhere else."""
    run_resp = seeded_client.post(
        "/api/v1/projections/run",
        json={"order_id": "WMT-100234", "projection_date": "2026-08-09"},
    )
    assert run_resp.status_code == 200, run_resp.text

    seeded_client.app.dependency_overrides[get_llm_client] = lambda: FailingChatClient()

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert resp.status_code == 202, resp.text

    status_resp = seeded_client.get("/api/v1/orders/WMT-100234/summary", params={"as_of_date": "2026-08-09"})
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "FAILED"
    assert body["summary"] is None
    assert body["error_message"] == "Fine summary generation failed upstream"
    assert _LEAKY_DETAIL not in status_resp.text
