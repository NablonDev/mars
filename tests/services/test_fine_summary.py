"""Tests for FineSummaryService, using a hand-written fake chat client instead of a LangChain mock."""

from datetime import date
from unittest.mock import patch

import pytest
from langchain_core.messages import ToolMessage

from app.agents.fine_summary_schema import FineSummaryOutput
from app.agents.prompts.fine_summary.v2 import PROMPT_VERSION
from app.core.exceptions import OrderNotFoundError
from app.repositories.fine_summary import FineSummaryRepository
from app.services.fine_projection import ProjectionResult, ViolationProjection
from app.services.fine_summary import (
    FineSummaryJob,
    FineSummaryService,
    InvalidAsOfDateError,
    NoProjectionExistsError,
    NoSummaryJobExistsError,
)


class _FakeAIMessage:
    def __init__(self, content: str | None, tool_calls: list[dict]) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeChatClient:
    """`tool_call_plan` is a list of tool-call lists, one per round the loop
    offers tools for -- an empty list means "no more calls, give the final
    answer". `final_content` is the tools-less final round's return value;
    `None` simulates the model returning nothing (a failure case)."""

    model_name = "fake-model"

    def __init__(
        self,
        tool_call_plan: list[list[dict]] | None = None,
        final_content: str | None = "Fine, all clear.",
        fail_invoke_on_round: int | None = None,
    ):
        self.tool_call_plan = tool_call_plan or []
        self.final_content = final_content
        # Index into ALL invoke() calls, not just tool-offering ones -- lets
        # a test target a provider failure on any specific round.
        self.fail_invoke_on_round = fail_invoke_on_round
        self.invocations: list[dict] = []
        self._optional_round = 0

    def invoke(self, messages, *, tools=None):
        invocation_index = len(self.invocations)
        self.invocations.append({"messages": messages, "tools": tools})
        if invocation_index == self.fail_invoke_on_round:
            raise RuntimeError("simulated upstream failure (e.g. openai.APITimeoutError)")

        if not tools:
            return _FakeAIMessage(content=self.final_content, tool_calls=[])

        calls = (
            self.tool_call_plan[self._optional_round]
            if self._optional_round < len(self.tool_call_plan)
            else []
        )
        self._optional_round += 1
        return _FakeAIMessage(content=None, tool_calls=calls)


def _seed_flat_rule_order(services, order_id: str = "ORD-EXP") -> None:
    """A flat-rule order with one OTIF_LATE/FLAT_FEE violation, no actual fines."""
    services.master_data.add_retailer("RET-EXP", "Retailer Exp", None, "SUM")
    services.master_data.add_sku("SKU-EXP", "MAT-EXP", None)
    services.master_data.add_location("LOC-EXP", None, None)
    services.orders.create_order(
        order_id=order_id,
        retailer_id="RET-EXP",
        sku_id="SKU-EXP",
        ship_from_location_id="LOC-EXP",
        order_qty=1000,
        unit_price=10.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    services.rules.add_rule(
        rule_id="RULE-EXP-FLAT",
        retailer_id="RET-EXP",
        violation_type="OTIF_LATE",
        calc_type="FLAT_FEE",
        rate=50.0,
    )
    services.projections.save_result(
        ProjectionResult(
            order_id=order_id,
            projection_date=date(2026, 8, 5),
            days_to_delivery=3,
            shortage_probability=0.05,
            delay_probability=0.05,
            violations=[
                ViolationProjection(
                    violation_type="OTIF_LATE",
                    rule_id="RULE-EXP-FLAT",
                    probability=0.05,
                    fine_if_realized=50.0,
                    expected_fine=2.5,
                )
            ],
            total_expected_fine=2.5,
            stacking_mode="SUM",
        )
    )


@pytest.fixture
def summary_repo(db_session) -> FineSummaryRepository:
    return FineSummaryRepository(db_session)


def _build_service(services, summary_repo, fake_llm) -> FineSummaryService:
    return FineSummaryService(
        orders=services.orders,
        rules=services.rules,
        master_data=services.master_data,
        projections=services.projections,
        summaries=summary_repo,
        prompt_registry=services.prompt_registry,
        llm=fake_llm,
    )


def _schedule_and_run(
    service: FineSummaryService,
    order_id: str,
    as_of_date: date | None = None,
    force_regenerate: bool = False,
) -> FineSummaryJob:
    """Runs get_or_schedule, then run_generation inline on a PENDING result --
    what a BackgroundTasks worker does, minus an actual background thread."""
    job = service.get_or_schedule(order_id, as_of_date=as_of_date, force_regenerate=force_regenerate)
    if job.status != "PENDING":
        return job
    service.run_generation(job.order_id, job.as_of_date, job.prompt_version)
    return service.get_status(job.order_id, as_of_date=job.as_of_date)


def test_order_not_found_raises_order_not_found_error(services, summary_repo):
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    with pytest.raises(OrderNotFoundError, match="NOPE"):
        service.get_or_schedule("NOPE")


def test_no_projection_exists_error_when_no_projected_fine_rows(services, summary_repo):
    services.master_data.add_retailer("RET-EMPTY", "Retailer Empty", None, "SUM")
    services.master_data.add_sku("SKU-EMPTY", "MAT-EMPTY", None)
    services.master_data.add_location("LOC-EMPTY", None, None)
    services.orders.create_order(
        order_id="ORD-EMPTY",
        retailer_id="RET-EMPTY",
        sku_id="SKU-EMPTY",
        ship_from_location_id="LOC-EMPTY",
        order_qty=100,
        unit_price=5.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    with pytest.raises(NoProjectionExistsError, match="ORD-EMPTY"):
        service.get_or_schedule("ORD-EMPTY")


def test_get_or_schedule_returns_pending_and_persists_a_pending_row_on_cache_miss(services, summary_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    job = service.get_or_schedule("ORD-EXP", as_of_date=date(2026, 8, 5))

    assert job.status == "PENDING"
    assert job.output is None
    # The LLM must never be called from get_or_schedule itself -- only
    # run_generation (the background job) calls it.
    assert fake_llm.invocations == []

    persisted = summary_repo.get_by_key("ORD-EXP", date(2026, 8, 5), PROMPT_VERSION)
    assert persisted["status"] == "PENDING"
    assert persisted["summary"] is None
    assert persisted["model_name"] is None


def test_mandatory_context_always_present(services, summary_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    job = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    assert job.status == "READY"
    # 1 optional round (breaks immediately, empty tool_call_plan) + 1 final round.
    assert len(fake_llm.invocations) == 2
    content = fake_llm.invocations[0]["messages"][1].content
    assert content.startswith("<DATA>")
    assert content.endswith("</DATA>")
    for expected_key in (
        "order",
        "current_projection_date",
        "stacking_mode",
        "active_rules",
        "daily_history",
        "shared_production_line",
        "other_open_orders_same_sku_location",
    ):
        assert f'"{expected_key}"' in content


def test_daily_history_is_bounded_to_as_of_date_not_the_full_table(services, summary_repo):
    """A later projection_date row for the same order must never leak into
    a summary requested for an earlier as_of_date -- otherwise "today" is
    ambiguous and the response can describe events from the future."""
    _seed_flat_rule_order(services)
    services.projections.save_result(
        ProjectionResult(
            order_id="ORD-EXP",
            projection_date=date(2026, 8, 9),
            days_to_delivery=0,
            shortage_probability=0.92,
            delay_probability=0.92,
            violations=[
                ViolationProjection(
                    violation_type="OTIF_LATE",
                    rule_id="RULE-EXP-FLAT",
                    probability=0.92,
                    fine_if_realized=50.0,
                    expected_fine=46.0,
                )
            ],
            total_expected_fine=46.0,
            stacking_mode="SUM",
        )
    )
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    content = fake_llm.invocations[0]["messages"][1].content
    assert '"2026-08-05"' in content
    assert '"2026-08-09"' not in content
    assert "46.0" not in content


def test_optional_tools_never_called_when_unrequested(services, summary_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    with (
        patch.object(
            services.orders, "list_actual_fines", wraps=services.orders.list_actual_fines
        ) as spy_fines,
        patch.object(services.rules, "list_rules", wraps=services.rules.list_rules) as spy_tiers,
    ):
        _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    spy_fines.assert_not_called()
    spy_tiers.assert_not_called()
    # The model was offered tools on its one optional round and chose not
    # to use any (empty tool_call_plan); the final round never offers tools.
    assert len(fake_llm.invocations) == 2
    assert fake_llm.invocations[0]["tools"]
    assert fake_llm.invocations[1]["tools"] is None


def test_cache_hit_skips_llm(services, summary_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    first = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))
    assert first.status == "READY"
    assert len(fake_llm.invocations) == 2

    second = service.get_or_schedule("ORD-EXP", as_of_date=date(2026, 8, 5))

    # Cache hit: READY immediately, no new LLM call, no PENDING row created.
    assert second.status == "READY"
    assert len(fake_llm.invocations) == 2
    assert second.output == first.output


def test_force_regenerate_skips_the_cache_check_and_calls_the_llm(services, summary_repo):
    """force_regenerate=True skips get_cached entirely -- shown here against
    a date with no prior cached row, so the LLM call is unambiguously
    because of force_regenerate, not an incidental cache miss."""
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    job = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5), force_regenerate=True)

    assert job.status == "READY"
    assert len(fake_llm.invocations) == 2


def test_force_regenerate_against_an_already_cached_key_replaces_the_row_in_place(
    services, summary_repo, db_session
):
    """force_regenerate=True against an exact-match key re-arms the existing
    row to PENDING rather than raising -- the new output is returned, and
    there's still exactly one row under that key afterwards, not two."""
    from sqlalchemy import func, select

    from app.models import FineSummary

    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient(final_content="Flat $2.50 expected delay fine.")
    service = _build_service(services, summary_repo, fake_llm)

    first_job = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))
    assert first_job.status == "READY"

    fake_llm.final_content = "Updated: carrier reliability improved, fine now $0."

    result = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5), force_regenerate=True)

    assert result.status == "READY"
    assert result.output.summary == fake_llm.final_content
    # 2 invocations per full generation (1 optional round + 1 final), x2 generations.
    assert len(fake_llm.invocations) == 4

    row_count = db_session.scalar(
        select(func.count())
        .select_from(FineSummary)
        .where(
            FineSummary.order_id == "ORD-EXP",
            FineSummary.as_of_date == date(2026, 8, 5),
        )
    )
    assert row_count == 1

    cached = summary_repo.get_cached("ORD-EXP", date(2026, 8, 5), PROMPT_VERSION)
    assert cached["summary"] == fake_llm.final_content


def test_cache_miss_on_prompt_version_bump(services, summary_repo):
    _seed_flat_rule_order(services)
    # Simulate a stale row persisted under an old prompt version.
    stale_agent_id = services.prompt_registry.ensure_registered(
        agent_name="fine_summary",
        prompt_version="v0-stale",
        module_path="app.agents.prompts.fine_summary.v0_stale",
    )
    summary_repo.mark_ready(
        order_id="ORD-EXP",
        as_of_date=date(2026, 8, 5),
        agent_id=stale_agent_id,
        prompt_version="v0-stale",
        model_name="old-model",
        summary="stale summary text",
    )

    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    job = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    # The stale row's prompt_version doesn't match the current
    # PROMPT_VERSION, so it must NOT be treated as a cache hit.
    assert job.status == "READY"
    assert len(fake_llm.invocations) == 2


def test_as_of_date_in_the_future_is_rejected(services, summary_repo):
    """Closes the unbounded-cache-key abuse vector: as_of_date is
    client-supplied, so it must be bounded to real projection history
    rather than accepted verbatim as a fresh cache key."""
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    with pytest.raises(InvalidAsOfDateError, match="future"):
        service.get_or_schedule("ORD-EXP", as_of_date=date(2099, 1, 1))

    # Rejected before any LLM call or cache write happens.
    assert fake_llm.invocations == []
    assert summary_repo.get_cached("ORD-EXP", date(2099, 1, 1), PROMPT_VERSION) is None


def test_as_of_date_before_earliest_projection_is_rejected(services, summary_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    with pytest.raises(InvalidAsOfDateError, match="earliest"):
        service.get_or_schedule("ORD-EXP", as_of_date=date(2020, 1, 1))

    assert fake_llm.invocations == []


def test_as_of_date_validation_is_not_bypassed_by_force_regenerate(services, summary_repo):
    """The abuse path the fix closes: varying as_of_date with
    force_regenerate=True to always miss cache and trigger a real LLM
    call. force_regenerate must not skip this check."""
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    with pytest.raises(InvalidAsOfDateError):
        service.get_or_schedule("ORD-EXP", as_of_date=date(2099, 1, 1), force_regenerate=True)

    assert fake_llm.invocations == []


def test_get_actual_fines_for_order_tool_cannot_be_pointed_at_a_different_order(services, summary_repo):
    """A model supplying a different order_id in the tool call must still
    get actual fines for the order under summarization (ORD-EXP) --
    GetActualFinesForOrderArgs doesn't accept order_id as a field at all."""
    _seed_flat_rule_order(services)
    # get_actual_fines_for_order is now DELIVERED-only server-side.
    services.orders.set_order_status("ORD-EXP", "DELIVERED")
    services.orders.create_order(
        order_id="ORD-OTHER",
        retailer_id="RET-EXP",
        sku_id="SKU-EXP",
        ship_from_location_id="LOC-EXP",
        order_qty=500,
        unit_price=10.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )

    tool_call_plan = [
        [{"id": "call-1", "name": "get_actual_fines_for_order", "args": {"order_id": "ORD-OTHER"}}]
    ]
    fake_llm = FakeChatClient(tool_call_plan=tool_call_plan)
    service = _build_service(services, summary_repo, fake_llm)

    with patch.object(
        services.orders, "list_actual_fines", wraps=services.orders.list_actual_fines
    ) as spy_fines:
        _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    # Called both by mandatory-context assembly (order is DELIVERED, so
    # actual_outcomes is populated directly) and by the tool call itself --
    # every call must still resolve to ORD-EXP, never the hallucinated
    # ORD-OTHER id from the tool call args.
    assert spy_fines.call_count >= 1
    for call in spy_fines.call_args_list:
        assert call.args == ("ORD-EXP",)


def test_get_tier_bands_for_rule_tool_cannot_be_pointed_at_a_different_retailers_rule(services, summary_repo):
    """A model pointing at a different retailer's TIERED rule_id must get
    the same "not found" result an invalid rule_id would -- rule_id is
    resolved only within the enclosing order's own retailer, never a
    system-wide scan, so another retailer's rate card never leaks."""
    _seed_flat_rule_order(services)
    services.master_data.add_retailer("RET-OTHER", "Retailer Other", None, "SUM")
    services.rules.add_rule(
        rule_id="RULE-OTHER-TIERED",
        retailer_id="RET-OTHER",
        violation_type="SHORTAGE",
        calc_type="TIERED",
        rate=0.0,
        tiers=[
            {"band_min": 0.0, "band_max": 0.1, "rate": 0.02},
            {"band_min": 0.1, "band_max": 1.0, "rate": 0.05},
        ],
    )

    tool_call_plan = [
        [{"id": "call-1", "name": "get_tier_bands_for_rule", "args": {"rule_id": "RULE-OTHER-TIERED"}}]
    ]
    fake_llm = FakeChatClient(tool_call_plan=tool_call_plan)
    service = _build_service(services, summary_repo, fake_llm)

    _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    tool_messages = [m for m in fake_llm.invocations[-1]["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert '"found": false' in tool_messages[0].content
    assert "RULE-OTHER-TIERED" in tool_messages[0].content
    # The other retailer's rate-card data must never reach the model.
    assert "0.02" not in tool_messages[0].content
    assert "0.05" not in tool_messages[0].content


def test_bounded_loop_marks_the_job_failed_after_max_tool_rounds(services, summary_repo):
    _seed_flat_rule_order(services)
    # Every optional round asks for the same tool, forever -- the model
    # never settles down to a final answer within the round budget.
    always_call_a_tool = [
        [{"id": f"call-{i}", "name": "get_carrier_reliability_detail", "args": {"carrier_id": "CAR-EXP"}}]
        for i in range(3)
    ]
    fake_llm = FakeChatClient(tool_call_plan=always_call_a_tool, final_content=None)
    service = _build_service(services, summary_repo, fake_llm)

    job = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    # run_generation never raises -- a background job with nothing
    # awaiting it must persist the failure, not throw it away.
    assert job.status == "FAILED"
    assert job.error_message == "Fine summary generation failed upstream"
    # 3 optional tool-calling rounds + the 1 final (empty-content) round.
    assert len(fake_llm.invocations) == 4


def test_provider_failure_on_an_early_tool_round_results_in_a_failed_job_not_a_crash(services, summary_repo):
    """Regression test found live against a real deployment: a transient
    provider failure on the FIRST optional round used to propagate as a
    raw exception instead of landing as a client-safe FAILED status."""
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient(fail_invoke_on_round=0)
    service = _build_service(services, summary_repo, fake_llm)

    job = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    assert job.status == "FAILED"
    assert job.error_message == "Fine summary generation failed upstream"
    # The raw exception text must never reach the persisted, client-facing
    # error_message -- only the generic client-safe message does.
    assert "simulated upstream failure" not in job.error_message
    # Failed on the very first invocation -- never even reached the final round.
    assert len(fake_llm.invocations) == 1


def test_force_regenerate_against_a_failed_row_can_recover(services, summary_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient(fail_invoke_on_round=0)
    service = _build_service(services, summary_repo, fake_llm)

    first = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))
    assert first.status == "FAILED"

    fake_llm.fail_invoke_on_round = None
    recovered = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5), force_regenerate=True)

    assert recovered.status == "READY"
    assert recovered.error_message is None


def test_get_status_raises_when_no_job_was_ever_scheduled(services, summary_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    with pytest.raises(NoSummaryJobExistsError, match="ORD-EXP"):
        service.get_status("ORD-EXP", as_of_date=date(2026, 8, 5))


def test_get_status_reports_pending_before_generation_runs(services, summary_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient()
    service = _build_service(services, summary_repo, fake_llm)

    service.get_or_schedule("ORD-EXP", as_of_date=date(2026, 8, 5))
    job = service.get_status("ORD-EXP", as_of_date=date(2026, 8, 5))

    assert job.status == "PENDING"
    assert job.output is None
    assert job.error_message is None


def test_ready_output_reconstructed_from_the_rows_own_columns(services, summary_repo):
    """FineSummaryOutput is rebuilt from the row's own columns, never
    parsed out of the persisted summary text (it's plain text, not JSON)."""
    _seed_flat_rule_order(services)
    fake_llm = FakeChatClient(final_content="The current total is $2.50 because of a flat OTIF fee.")
    service = _build_service(services, summary_repo, fake_llm)

    job = _schedule_and_run(service, "ORD-EXP", as_of_date=date(2026, 8, 5))

    assert job.status == "READY"
    assert isinstance(job.output, FineSummaryOutput)
    assert job.output.order_id == "ORD-EXP"
    assert job.output.as_of_date == date(2026, 8, 5)
    assert job.output.prompt_version == PROMPT_VERSION
    assert job.output.model_name == "fake-model"
    assert job.output.summary == "The current total is $2.50 because of a flat OTIF fee."
