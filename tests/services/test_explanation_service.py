"""
Tests for ExplanationService, using a hand-written fake StructuredChatClient
(not a LangChain mock) so these tests aren't coupled to LangChain's wire
format. No real Azure OpenAI call is ever reachable from this file.
"""

from datetime import date
from unittest.mock import patch

import pytest

from app.agents.base import ChatTurnResult, ToolCall
from app.agents.explanation_schema import ProjectionExplanationOutput
from app.agents.prompts.explain_projection.v1 import PROMPT_VERSION
from app.core.exceptions import OrderNotFoundError
from app.engine import ProjectionResult, ViolationProjection
from app.repositories.explanation_repository import ExplanationRepository
from app.services.explanation_service import (
    ExplanationService,
    InvalidAsOfDateError,
    NoProjectionExistsError,
    ToolLoopExhaustedError,
)


class FakeStructuredChatClient:
    """Programmable fake: `tool_call_plan` is a list of tool-call lists,
    one per optional tool-calling round (empty list in a round means "no
    more tools, give the final answer"). `final_output` is returned by
    `call_structured`, or a RuntimeError is raised if it's None."""

    model_name = "fake-model"

    def __init__(
        self,
        tool_call_plan: list[list[ToolCall]] | None = None,
        final_output=None,
        fail_call_with_tools_on_round: int | None = None,
    ):
        self.tool_call_plan = tool_call_plan or []
        self.final_output = final_output
        # Simulates a real provider/network failure (rate limit, timeout,
        # an SDK exception after its own retries are exhausted) on a
        # specific *optional* round -- not the final call_structured round,
        # which already had its own try/except. Zero-indexed: 0 is the
        # first call_with_tools invocation.
        self.fail_call_with_tools_on_round = fail_call_with_tools_on_round
        self.call_with_tools_invocations: list[dict] = []
        self.call_structured_invocations: list[dict] = []
        self._round = 0

    def call_with_tools(self, system_prompt, messages, tools):
        self.call_with_tools_invocations.append(
            {"system_prompt": system_prompt, "messages": messages, "tools": tools}
        )
        if self._round == self.fail_call_with_tools_on_round:
            raise RuntimeError("simulated upstream failure (e.g. openai.APITimeoutError)")
        calls = self.tool_call_plan[self._round] if self._round < len(self.tool_call_plan) else []
        self._round += 1
        return ChatTurnResult(tool_calls=calls, content=None)

    def call_structured(self, system_prompt, messages, output_schema):
        self.call_structured_invocations.append(
            {"system_prompt": system_prompt, "messages": messages, "output_schema": output_schema}
        )
        if self.final_output is None:
            raise RuntimeError("fake client has no final_output configured for this round")
        return self.final_output


def _sample_output(order_id: str = "ORD-EXP", as_of: date = date(2026, 8, 5)) -> ProjectionExplanationOutput:
    return ProjectionExplanationOutput(
        order_id=order_id,
        as_of_date=as_of,
        headline_summary="Flat $2.50 expected delay fine.",
        current_total_expected_fine=2.5,
        stacking_mode="SUM",
        violations=[],
        day_by_day_narrative=[],
        key_sensitivity_factors=[],
        caveats=[],
    )


def _seed_flat_rule_order(services, order_id: str = "ORD-EXP") -> None:
    """A flat-rule order with a single OTIF_LATE/FLAT_FEE violation and no
    actual fines -- deliberately the simplest case, so the
    optional-tools-never-called assertion has nothing that should trigger
    a real tool call."""
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
def explanation_repo(db_session) -> ExplanationRepository:
    return ExplanationRepository(db_session)


def _build_service(services, explanation_repo, fake_llm) -> ExplanationService:
    return ExplanationService(
        orders=services.orders,
        rules=services.rules,
        master_data=services.master_data,
        projections=services.projections,
        explanations=explanation_repo,
        llm=fake_llm,
    )


def test_order_not_found_raises_order_not_found_error(services, explanation_repo):
    fake_llm = FakeStructuredChatClient(final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    with pytest.raises(OrderNotFoundError, match="NOPE"):
        service.explain_order("NOPE")


def test_no_projection_exists_error_when_no_projected_fine_rows(services, explanation_repo):
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
    fake_llm = FakeStructuredChatClient(final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    with pytest.raises(NoProjectionExistsError, match="ORD-EMPTY"):
        service.explain_order("ORD-EMPTY")


def test_mandatory_context_always_present(services, explanation_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    assert len(fake_llm.call_structured_invocations) == 1
    content = fake_llm.call_structured_invocations[0]["messages"][0]["content"]
    assert content.startswith("<DATA>")
    assert content.endswith("</DATA>")
    for expected_key in (
        "order",
        "stacking_mode",
        "projection_history",
        "fine_rules",
        "confirmations",
        "shipments",
        "demand_exceptions",
        "production_status_history",
    ):
        assert f'"{expected_key}"' in content


def test_optional_tools_never_called_when_unrequested(services, explanation_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    with (
        patch.object(
            services.orders, "list_actual_fines", wraps=services.orders.list_actual_fines
        ) as spy_fines,
        patch.object(services.rules, "list_rules", wraps=services.rules.list_rules) as spy_tiers,
    ):
        service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    spy_fines.assert_not_called()
    spy_tiers.assert_not_called()
    # The model was still offered the tools -- it simply chose not to use them.
    assert len(fake_llm.call_with_tools_invocations) == 1
    assert fake_llm.call_with_tools_invocations[0]["tools"]


def test_cache_hit_skips_llm(services, explanation_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    first = service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))
    assert len(fake_llm.call_structured_invocations) == 1

    second = service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    # No new LLM call at all on the cache hit.
    assert len(fake_llm.call_structured_invocations) == 1
    assert len(fake_llm.call_with_tools_invocations) == 1
    assert second == first


def test_force_regenerate_skips_the_cache_check_and_calls_the_llm(services, explanation_repo):
    """force_regenerate=True skips the get_cached lookup entirely -- shown
    here against a date with no prior cached row, so the LLM call is
    unambiguously because of force_regenerate, not because of a cache
    miss that would have happened anyway."""
    _seed_flat_rule_order(services)
    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5), force_regenerate=True)

    assert len(fake_llm.call_structured_invocations) == 1


def test_force_regenerate_against_an_already_cached_key_replaces_the_row_in_place(
    services, explanation_repo, db_session
):
    """force_regenerate=True against an exact-match
    (order_id, as_of_date, prompt_version) key used to raise
    IntegrityError, because ExplanationRepository.save is deliberately
    insert-only (see its docstring) -- but that's the one scenario the
    flag exists for. ExplanationService now routes the force_regenerate
    persist through ExplanationRepository.replace instead, which updates
    the existing row in place: the LLM is re-run, the new output is
    returned, and there is still exactly one row under that key
    afterwards -- not two, and not an IntegrityError."""
    from sqlalchemy import func, select

    from app.agents.prompts.explain_projection.v1 import PROMPT_VERSION
    from app.models import ProjectionExplanation

    _seed_flat_rule_order(services)
    first_output = _sample_output()
    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=first_output)
    service = _build_service(services, explanation_repo, fake_llm)

    service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    second_output = _sample_output()
    second_output.headline_summary = "Updated: carrier reliability improved, fine now $0."
    fake_llm.final_output = second_output

    result = service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5), force_regenerate=True)

    assert result.headline_summary == second_output.headline_summary
    assert len(fake_llm.call_structured_invocations) == 2

    row_count = db_session.scalar(
        select(func.count())
        .select_from(ProjectionExplanation)
        .where(
            ProjectionExplanation.order_id == "ORD-EXP",
            ProjectionExplanation.as_of_date == date(2026, 8, 5),
        )
    )
    assert row_count == 1

    cached = explanation_repo.get_cached("ORD-EXP", date(2026, 8, 5), PROMPT_VERSION)
    assert cached["explanation"]["headline_summary"] == second_output.headline_summary


def test_cache_miss_on_prompt_version_bump(services, explanation_repo):
    _seed_flat_rule_order(services)
    # Simulate a stale row persisted under an old prompt version.
    explanation_repo.save(
        order_id="ORD-EXP",
        as_of_date=date(2026, 8, 5),
        prompt_version="v0-stale",
        context_hash="deadbeef",
        model_name="old-model",
        explanation=_sample_output().model_dump(mode="json"),
    )

    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    # The stale row's prompt_version doesn't match the current
    # PROMPT_VERSION, so it must NOT be treated as a cache hit.
    assert len(fake_llm.call_structured_invocations) == 1


def test_as_of_date_in_the_future_is_rejected(services, explanation_repo):
    """Closes the unbounded-cache-key abuse vector: as_of_date is a
    client-supplied field, so it must be bounded to real projection
    history rather than accepted verbatim as a fresh cache key."""
    _seed_flat_rule_order(services)
    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    with pytest.raises(InvalidAsOfDateError, match="future"):
        service.explain_order("ORD-EXP", as_of_date=date(2099, 1, 1))

    # Rejected before any LLM call or cache write happens.
    assert fake_llm.call_with_tools_invocations == []
    assert fake_llm.call_structured_invocations == []
    assert explanation_repo.get_cached("ORD-EXP", date(2099, 1, 1), PROMPT_VERSION) is None


def test_as_of_date_before_earliest_projection_is_rejected(services, explanation_repo):
    _seed_flat_rule_order(services)
    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    with pytest.raises(InvalidAsOfDateError, match="earliest"):
        service.explain_order("ORD-EXP", as_of_date=date(2020, 1, 1))

    assert fake_llm.call_with_tools_invocations == []
    assert fake_llm.call_structured_invocations == []


def test_as_of_date_validation_is_not_bypassed_by_force_regenerate(services, explanation_repo):
    """The specific abuse path the fix closes: varying as_of_date while
    also setting force_regenerate=True to always miss cache and always
    trigger a real LLM call. force_regenerate must not skip this check."""
    _seed_flat_rule_order(services)
    fake_llm = FakeStructuredChatClient(tool_call_plan=[], final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    with pytest.raises(InvalidAsOfDateError):
        service.explain_order("ORD-EXP", as_of_date=date(2099, 1, 1), force_regenerate=True)

    assert fake_llm.call_structured_invocations == []


def test_get_actual_fines_for_order_tool_cannot_be_pointed_at_a_different_order(services, explanation_repo):
    """A hallucinating or adversarial model supplies a different
    order_id in the tool call arguments -- the tool must still look up
    actual fines for the order actually under explanation (ORD-EXP), and
    must ignore the model-supplied order_id entirely rather than
    validating-and-rejecting it, since GetActualFinesForOrderArgs no
    longer accepts order_id as a field at all (see explanation_tools.py)."""
    _seed_flat_rule_order(services)
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
        [
            ToolCall(
                id="call-1",
                name="get_actual_fines_for_order",
                arguments={"order_id": "ORD-OTHER"},
            )
        ]
    ]
    fake_llm = FakeStructuredChatClient(tool_call_plan=tool_call_plan, final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    with patch.object(
        services.orders, "list_actual_fines", wraps=services.orders.list_actual_fines
    ) as spy_fines:
        service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    spy_fines.assert_called_once_with("ORD-EXP")


def test_get_tier_bands_for_rule_tool_cannot_be_pointed_at_a_different_retailers_rule(
    services, explanation_repo
):
    """A hallucinating or adversarial model supplies a different
    retailer's TIERED rule_id in the tool call arguments -- the tool
    must not return that retailer's tier bands (sensitive rate-card
    data) just because the model asked for it by id. It must come back
    as the same "not found" result a genuinely invalid rule_id would
    get, since ExplanationService._execute_tool resolves rule_id only
    within the enclosing order's own retailer (RET-EXP) via
    get_rules_for_retailer, never a system-wide, unfiltered scan."""
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
        [
            ToolCall(
                id="call-1",
                name="get_tier_bands_for_rule",
                arguments={"rule_id": "RULE-OTHER-TIERED"},
            )
        ]
    ]
    fake_llm = FakeStructuredChatClient(tool_call_plan=tool_call_plan, final_output=_sample_output())
    service = _build_service(services, explanation_repo, fake_llm)

    service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    tool_messages = [m for m in fake_llm.call_structured_invocations[0]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert '"found": false' in tool_messages[0]["content"]
    assert "RULE-OTHER-TIERED" in tool_messages[0]["content"]
    # The other retailer's rate-card data must never reach the model.
    assert "0.02" not in tool_messages[0]["content"]
    assert "0.05" not in tool_messages[0]["content"]


def test_bounded_loop_raises_after_max_tool_rounds(services, explanation_repo):
    _seed_flat_rule_order(services)
    # Every optional round asks for the same tool, forever -- the model
    # never settles down to a final answer within the round budget.
    always_call_a_tool = [
        [ToolCall(id=f"call-{i}", name="get_carrier_reliability_detail", arguments={"carrier_id": "CAR-EXP"})]
        for i in range(3)
    ]
    fake_llm = FakeStructuredChatClient(tool_call_plan=always_call_a_tool, final_output=None)
    service = _build_service(services, explanation_repo, fake_llm)

    with pytest.raises(ToolLoopExhaustedError, match="ORD-EXP"):
        service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    # 3 optional tool-calling rounds + the 1 forced (failing) structured round.
    assert len(fake_llm.call_with_tools_invocations) == 3
    assert len(fake_llm.call_structured_invocations) == 1


def test_provider_failure_on_an_early_tool_round_is_not_a_raw_500(services, explanation_repo):
    """Regression test: found live against a real (slow) Azure deployment,
    not by any mocked-client test -- a transient provider failure on the
    FIRST optional round (before any tool result is even processed) used
    to propagate straight past this method as a raw, unhandled exception
    instead of the documented ToolLoopExhaustedError (502). The bug was a
    try/except that wrapped only the final call_structured call, not the
    call_with_tools rounds above it."""
    _seed_flat_rule_order(services)
    fake_llm = FakeStructuredChatClient(final_output=_sample_output(), fail_call_with_tools_on_round=0)
    service = _build_service(services, explanation_repo, fake_llm)

    with pytest.raises(ToolLoopExhaustedError, match="ORD-EXP"):
        service.explain_order("ORD-EXP", as_of_date=date(2026, 8, 5))

    # Failed on the very first round -- never even reached call_structured.
    assert len(fake_llm.call_with_tools_invocations) == 1
    assert len(fake_llm.call_structured_invocations) == 0
