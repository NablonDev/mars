"""Integration tests for the durable job_item wiring behind the on-demand
fine-mitigation-summary endpoints. Full mirror of
tests/unit/api/test_api_fine_projection_summary_job_queue.py."""

from datetime import date
from uuid import UUID

from sqlalchemy import select

from app.agents.fine_mitigation.prompts.v1 import PROMPT_VERSION
from app.api.dependencies import get_fine_mitigation_summary_job_runner, get_llm_client
from app.core.config import Settings
from app.models import JobItem
from app.repositories.job_queue import JobQueueRepository


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
        raise RuntimeError("simulated upstream failure")


_SAMPLE_SUMMARY_TEXT = "Accepting the fine is currently the best option."


def _override_llm_client(client, summary_text: str = _SAMPLE_SUMMARY_TEXT) -> None:
    client.app.dependency_overrides[get_llm_client] = lambda: FakeChatClient(summary_text)


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


def _summary_job_items(db_session, order_id: str) -> list[JobItem]:
    return list(
        db_session.scalars(
            select(JobItem).where(
                JobItem.order_id == order_id, JobItem.task_type == "MITIGATION_SUMMARY_REGEN"
            )
        ).all()
    )


def test_cache_miss_creates_a_job_item_and_dispatches(seeded_client, db_session):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)
    _override_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert resp.status_code == 202, resp.text

    items = _summary_job_items(db_session, "WMT-100234")
    assert len(items) == 1
    item = items[0]
    assert item.task_type == "MITIGATION_SUMMARY_REGEN"
    assert item.dispatched_at is not None
    assert item.status == "SUCCEEDED"


def test_cache_hit_creates_no_job_item(seeded_client, db_session):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)
    _override_llm_client(seeded_client)

    first = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert first.status_code == 202, first.text
    assert len(_summary_job_items(db_session, "WMT-100234")) == 1

    second = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert second.status_code == 200, second.text
    assert len(_summary_job_items(db_session, "WMT-100234")) == 1


def test_cache_miss_response_body_is_unchanged_by_the_job_queue(seeded_client):
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


def test_background_job_marks_job_item_dead_on_failure(seeded_client, db_session):
    _run_projection(seeded_client)
    _run_mitigation_options(seeded_client)
    seeded_client.app.dependency_overrides[get_llm_client] = lambda: FailingChatClient()

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        json={"as_of_date": "2026-08-09"},
    )
    assert resp.status_code == 202, resp.text

    items = _summary_job_items(db_session, "WMT-100234")
    assert len(items) == 1
    item = items[0]
    assert item.status == "DEAD"
    assert item.last_error_code == "SUMMARY_GENERATION_FAILED"

    status_resp = seeded_client.get(
        "/api/v1/orders/WMT-100234/mitigation-summary",
        params={"as_of_date": "2026-08-09"},
    )
    assert status_resp.json()["status"] == "FAILED"


def test_mitigation_run_endpoint_cache_miss_also_creates_a_job_item(seeded_client, db_session):
    """POST /orders/{order_id}/mitigation-run: mitigation-options stays
    inline/sync, but the summary half follows the same durable-queue path
    as POST /orders/{order_id}/mitigation-summary."""
    _run_projection(seeded_client)
    _override_llm_client(seeded_client)

    resp = seeded_client.post(
        "/api/v1/orders/WMT-100234/mitigation-run",
        json={"projection_date": "2026-08-09"},
    )
    assert resp.status_code == 202, resp.text

    items = _summary_job_items(db_session, "WMT-100234")
    assert len(items) == 1
    assert items[0].status == "SUCCEEDED"


def test_lost_claim_does_not_execute_generation(database):
    """A job_item already claimed by another worker must not be executed
    a second time by this callable -- it logs and returns."""
    with database.session() as session:
        repo = JobQueueRepository(session)
        run = repo.create_run(run_type="ON_DEMAND", projection_date=date(2026, 8, 9))
        item = repo.enqueue(
            run["id"], "ORD-LOST-CLAIM", date(2026, 8, 9), "MITIGATION_SUMMARY_REGEN", max_attempts=5
        )
        assert item is not None
        job_item_id: UUID = item["id"]
        claimed = repo.claim_batch("other-worker", limit=1, job_item_ids=[job_item_id])
        assert len(claimed) == 1

    class _ExplodingLLMClient:
        model_name = "fake-model"

        def invoke(self, messages, *, tools=None):
            raise AssertionError("LLM must not be invoked when the claim is lost")

    run_job = get_fine_mitigation_summary_job_runner(
        database=database, llm=_ExplodingLLMClient(), settings=Settings()
    )

    run_job("ORD-LOST-CLAIM", date(2026, 8, 9), PROMPT_VERSION, job_item_id)

    with database.session() as session:
        row = session.get(JobItem, job_item_id)
        assert row is not None
        assert row.status == "RUNNING"
        assert row.locked_by == "other-worker"
