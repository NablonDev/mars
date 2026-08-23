"""Integration tests for the fine-mitigation-summary endpoints; full mirror
of tests/unit/api/test_api_fine_projection_summaries.py's structure."""

from app.agents.fine_mitigation.prompts.v1 import PROMPT_VERSION
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
    model_name = "fake-model"

    def invoke(self, messages, *, tools=None):
        raise RuntimeError(_LEAKY_DETAIL)


_LEAKY_DETAIL = "simulated upstream failure: secret vendor detail XYZ123"

_SAMPLE_SUMMARY_TEXT = (
    "Accepting the projected fine costs $540.00; speeding up production would cost $120 and save $300 net."
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


def _run_projection(seeded_client, order_id: str = "WMT-100234", projection_date: str = "2026-08-09"):
    resp = seeded_client.post(
        f"/api/v1/orders/{order_id}/projections",
        json={"projection_date": projection_date},
    )
    assert resp.status_code == 201, resp.text


def _run_mitigation_options(seeded_client, order_id: str = "WMT-100234", projection_date: str = "2026-08-09"):
    resp = seeded_client.post(
        f"/api/v1/orders/{order_id}/mitigation-options",
        json={"projection_date": projection_date},
    )
    assert resp.status_code == 201, resp.text


def test_fine_mitigation_summary_post_404s_for_unknown_order(seeded_client):
    _override_llm_client(seeded_client)

    resp = seeded_client.post("/api/v1/orders/NOPE/mitigation-summary", json={})

    assert resp.status_code == 404


def test_fine_mitigation_summary_post_422s_when_no_mitigation_options_exist_yet(seeded_client):
    _override_llm_client(seeded_client)

    resp = seeded_client.post("/api/v1/orders/WMT-100234/mitigation-summary", json={})

    assert resp.status_code == 422


def test_fine_mitigation_summary_get_404s_for_unknown_order(seeded_client):
    resp = seeded_client.get("/api/v1/orders/NOPE/mitigation-summary")

    assert resp.status_code == 404


def test_fine_mitigation_summary_get_422s_when_no_mitigation_options_exist_yet(seeded_client):
    resp = seeded_client.get("/api/v1/orders/WMT-100234/mitigation-summary")

    assert resp.status_code == 422


def test_fine_mitigation_summary_get_404s_when_no_job_was_ever_scheduled(seeded_client):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)

    resp = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary", params={"as_of_date": "2026-08-09"}
    )

    assert resp.status_code == 404


def test_fine_mitigation_summary_202s_then_ready_on_poll(seeded_client):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)
    _override_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09"},
    )

    assert resp.status_code == 202, resp.text
    assert resp.json() == {
        "order_id": "WMT-100234",
        "as_of_date": "2026-08-09",
        "prompt_version": PROMPT_VERSION,
        "status": "PENDING",
        "summary": None,
        "error_message": None,
    }

    status_resp = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary", params={"as_of_date": "2026-08-09"}
    )
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

    _exploding_llm_client(seeded_client)

    cached_resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert cached_resp.status_code == 200
    assert cached_resp.json() == summary


def test_fine_mitigation_summary_422s_for_an_as_of_date_in_the_future(seeded_client):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)
    _exploding_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2099-01-01"},
    )

    assert resp.status_code == 422, resp.text


def test_fine_mitigation_summary_422s_for_an_as_of_date_before_the_earliest_options_date(seeded_client):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)
    _exploding_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2000-01-01"},
    )

    assert resp.status_code == 422, resp.text


def test_fine_mitigation_summary_force_regenerate_against_an_existing_ready_row_succeeds(seeded_client):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)

    _override_llm_client(seeded_client)
    first_resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert first_resp.status_code == 202, first_resp.text
    first_status = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary", params={"as_of_date": "2026-08-09"}
    )
    assert first_status.json()["status"] == "READY"

    regenerated_text = "Regenerated: capacity boost cost dropped, SPEED_UP_PRODUCTION now wins."
    _override_llm_client(seeded_client, regenerated_text)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09", "force_regenerate": True},
    )
    assert resp.status_code == 202, resp.text

    final_status = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary", params={"as_of_date": "2026-08-09"}
    )
    assert final_status.status_code == 200
    body = final_status.json()
    assert body["status"] == "READY"
    assert body["summary"]["summary"] == regenerated_text
    assert body["summary"]["summary"] != first_status.json()["summary"]["summary"]


def test_fine_mitigation_summary_background_job_failure_surfaces_as_failed_status_not_a_raw_500(
    seeded_client,
):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)

    seeded_client.app.dependency_overrides[get_llm_client] = lambda: FailingChatClient()

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert resp.status_code == 202, resp.text

    status_resp = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary", params={"as_of_date": "2026-08-09"}
    )
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "FAILED"
    assert body["summary"] is None
    assert body["error_message"] == "Fine mitigation summary generation failed upstream"
    assert _LEAKY_DETAIL not in status_resp.text
