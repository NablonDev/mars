"""Tests for FineMitigationSummaryService, mirroring
tests/unit/services/test_fine_projection_summary.py's structure for the
mitigation-summary feature."""

from __future__ import annotations

from contextlib import suppress
from datetime import date

import pytest

from app.agents.fine_mitigation.prompts.v1 import PROMPT_VERSION
from app.core.exceptions import (
    InvalidAsOfDateError,
    NoMitigationOptionsExistError,
    NoSummaryJobExistsError,
    OrderNotFoundError,
    ToolLoopExhaustedError,
)
from app.repositories.fine_mitigation.mitigation import MitigationResultRepository
from app.repositories.fine_mitigation.summary import FineMitigationSummaryRepository
from app.services.fine_mitigation.summary import FineMitigationSummaryJob, FineMitigationSummaryService
from app.services.fine_mitigation.types import MitigationOption


class _FakeAIMessage:
    def __init__(self, content: str | None, tool_calls: list[dict]) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeChatClient:
    model_name = "fake-model"

    def __init__(
        self,
        tool_call_plan: list[list[dict]] | None = None,
        final_content: str | None = "Accepting the fine is currently the best option.",
        fail_invoke_on_round: int | None = None,
    ):
        self.tool_call_plan = tool_call_plan or []
        self.final_content = final_content
        self.fail_invoke_on_round = fail_invoke_on_round
        self.invocations: list[dict] = []
        self._optional_round = 0

    def invoke(self, messages, *, tools=None):
        invocation_index = len(self.invocations)
        self.invocations.append({"messages": messages, "tools": tools})
        if invocation_index == self.fail_invoke_on_round:
            raise RuntimeError("simulated upstream failure")

        if not tools:
            return _FakeAIMessage(content=self.final_content, tool_calls=[])

        calls = (
            self.tool_call_plan[self._optional_round]
            if self._optional_round < len(self.tool_call_plan)
            else []
        )
        self._optional_round += 1
        return _FakeAIMessage(content=None, tool_calls=calls)


def _seed_order(services, order_id: str = "ORD-MITSUM") -> None:
    services.master_data.add_retailer("RET-MITSUM", "Retailer MitSum", None, "SUM")
    services.master_data.add_sku("SKU-MITSUM", "MAT-MITSUM", None)
    services.master_data.add_location("LOC-MITSUM", None, None)
    services.orders.create_order(
        order_id=order_id,
        retailer_id="RET-MITSUM",
        sku_id="SKU-MITSUM",
        ship_from_location_id="LOC-MITSUM",
        order_qty=1000,
        unit_price=10.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )


def _seed_options(mitigation_result_repo, order_id: str, projection_date: date) -> None:
    mitigation_result_repo.save_results(
        order_id,
        projection_date,
        [
            MitigationOption(
                action="ACCEPT",
                projected_fine_after=100.0,
                action_cost=0.0,
                net_saving=0.0,
                risk_level="HIGH",
                confidence="CONFIRMED",
                rationale="Pay the projected fine as-is.",
            ),
            MitigationOption(
                action="SPEED_UP_PRODUCTION",
                projected_fine_after=20.0,
                action_cost=30.0,
                net_saving=50.0,
                risk_level="LOW",
                confidence="CONFIRMED",
                rationale="Closes the shortfall with extra capacity.",
            ),
        ],
    )


@pytest.fixture
def mitigation_result_repo(db_session) -> MitigationResultRepository:
    return MitigationResultRepository(db_session)


@pytest.fixture
def mitigation_summary_repo(db_session) -> FineMitigationSummaryRepository:
    return FineMitigationSummaryRepository(db_session)


def _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm):
    return FineMitigationSummaryService(
        orders=services.orders,
        master_data=services.master_data,
        mitigation_results=mitigation_result_repo,
        summaries=mitigation_summary_repo,
        prompt_registry=services.prompt_registry,
        llm=fake_llm,
    )


def _schedule_and_run(
    service: FineMitigationSummaryService,
    order_id: str,
    as_of_date: date | None = None,
    force_regenerate: bool = False,
) -> FineMitigationSummaryJob:
    job = service.get_or_schedule(order_id, as_of_date=as_of_date, force_regenerate=force_regenerate)
    if job.status != "PENDING":
        return job
    with suppress(Exception):
        service.run_generation(job.order_id, job.as_of_date, job.prompt_version)
    return service.get_status(job.order_id, as_of_date=job.as_of_date)


def test_order_not_found_raises_order_not_found_error(
    services, mitigation_result_repo, mitigation_summary_repo
):
    fake_llm = FakeChatClient()
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    with pytest.raises(OrderNotFoundError, match="NOPE"):
        service.get_or_schedule("NOPE")


def test_no_mitigation_options_exist_error(services, mitigation_result_repo, mitigation_summary_repo):
    _seed_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    with pytest.raises(NoMitigationOptionsExistError, match="ORD-MITSUM"):
        service.get_or_schedule("ORD-MITSUM")


def test_get_or_schedule_returns_pending_on_cache_miss(
    services, mitigation_result_repo, mitigation_summary_repo
):
    _seed_order(services)
    _seed_options(mitigation_result_repo, "ORD-MITSUM", date(2026, 8, 5))
    fake_llm = FakeChatClient()
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    job = service.get_or_schedule("ORD-MITSUM", as_of_date=date(2026, 8, 5))

    assert job.status == "PENDING"
    assert fake_llm.invocations == []
    persisted = mitigation_summary_repo.get_by_key("ORD-MITSUM", date(2026, 8, 5), PROMPT_VERSION)
    assert persisted["status"] == "PENDING"


def test_mandatory_context_contains_ranked_options(services, mitigation_result_repo, mitigation_summary_repo):
    _seed_order(services)
    _seed_options(mitigation_result_repo, "ORD-MITSUM", date(2026, 8, 5))
    fake_llm = FakeChatClient()
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    job = _schedule_and_run(service, "ORD-MITSUM", as_of_date=date(2026, 8, 5))

    assert job.status == "READY"
    content = fake_llm.invocations[0]["messages"][1].content
    assert content.startswith("<DATA>")
    for expected_key in (
        "order",
        "current_projection_date",
        "current_total_expected_fine",
        "stacking_mode",
        "mitigation_options",
    ):
        assert f'"{expected_key}"' in content
    assert '"SPEED_UP_PRODUCTION"' in content


def test_cache_hit_skips_llm(services, mitigation_result_repo, mitigation_summary_repo):
    _seed_order(services)
    _seed_options(mitigation_result_repo, "ORD-MITSUM", date(2026, 8, 5))
    fake_llm = FakeChatClient()
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    first = _schedule_and_run(service, "ORD-MITSUM", as_of_date=date(2026, 8, 5))
    assert first.status == "READY"
    assert len(fake_llm.invocations) == 2

    second = service.get_or_schedule("ORD-MITSUM", as_of_date=date(2026, 8, 5))
    assert second.status == "READY"
    assert len(fake_llm.invocations) == 2
    assert second.output == first.output


def test_force_regenerate_replaces_the_row_in_place(
    services, mitigation_result_repo, mitigation_summary_repo
):
    _seed_order(services)
    _seed_options(mitigation_result_repo, "ORD-MITSUM", date(2026, 8, 5))
    fake_llm = FakeChatClient(final_content="First narrative.")
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    first = _schedule_and_run(service, "ORD-MITSUM", as_of_date=date(2026, 8, 5))
    assert first.status == "READY"

    fake_llm.final_content = "Regenerated narrative."
    second = _schedule_and_run(service, "ORD-MITSUM", as_of_date=date(2026, 8, 5), force_regenerate=True)

    assert second.status == "READY"
    assert second.output.summary == "Regenerated narrative."
    assert second.output.summary != first.output.summary


def test_run_generation_persists_failed_status_and_reraises_on_tool_loop_exhaustion(
    services, mitigation_result_repo, mitigation_summary_repo
):
    _seed_order(services)
    _seed_options(mitigation_result_repo, "ORD-MITSUM", date(2026, 8, 5))
    fake_llm = FakeChatClient(fail_invoke_on_round=0)
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    job = service.get_or_schedule("ORD-MITSUM", as_of_date=date(2026, 8, 5))
    assert job.status == "PENDING"

    with pytest.raises(ToolLoopExhaustedError):
        service.run_generation(job.order_id, job.as_of_date, job.prompt_version)

    row = mitigation_summary_repo.get_by_key("ORD-MITSUM", date(2026, 8, 5), PROMPT_VERSION)
    assert row["status"] == "FAILED"
    assert row["error_message"] == "Fine mitigation summary generation failed upstream"


def test_get_status_raises_no_mitigation_summary_job_exists_when_never_posted(
    services, mitigation_result_repo, mitigation_summary_repo
):
    _seed_order(services)
    _seed_options(mitigation_result_repo, "ORD-MITSUM", date(2026, 8, 5))
    fake_llm = FakeChatClient()
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    with pytest.raises(NoSummaryJobExistsError):
        service.get_status("ORD-MITSUM", as_of_date=date(2026, 8, 5))


def test_as_of_date_in_the_future_raises_invalid_as_of_date(
    services, mitigation_result_repo, mitigation_summary_repo
):
    _seed_order(services)
    _seed_options(mitigation_result_repo, "ORD-MITSUM", date(2026, 8, 5))
    fake_llm = FakeChatClient()
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    with pytest.raises(InvalidAsOfDateError):
        service.get_or_schedule("ORD-MITSUM", as_of_date=date(2099, 1, 1))


def test_as_of_date_before_earliest_options_date_raises_invalid_as_of_date(
    services, mitigation_result_repo, mitigation_summary_repo
):
    _seed_order(services)
    _seed_options(mitigation_result_repo, "ORD-MITSUM", date(2026, 8, 5))
    fake_llm = FakeChatClient()
    service = _build_service(services, mitigation_result_repo, mitigation_summary_repo, fake_llm)

    with pytest.raises(InvalidAsOfDateError):
        service.get_or_schedule("ORD-MITSUM", as_of_date=date(2000, 1, 1))
