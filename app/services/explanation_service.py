"""
Orchestrates one LLM-generated projection explanation: assemble the
mandatory context, check the persisted audit-trail cache, run a bounded
tool-calling loop against a `StructuredChatClient`, and persist the
result. Deliberately does NOT call `ProjectionService.run_for_order` or
`app.engine.project_order` -- explaining an existing projection is
read-heavy and must never have the side effect of writing a new
`fact_projected_fine` row just because a client viewed an explanation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.agents.base import StructuredChatClient
from app.agents.explanation_schema import ProjectionExplanationOutput
from app.agents.prompts.explain_projection.v1 import PROMPT_VERSION, SYSTEM_PROMPT
from app.agents.tools.explanation_tools import (
    EXPLANATION_TOOL_SCHEMAS,
    GetActualFinesForOrderArgs,
    GetCarrierReliabilityDetailArgs,
    GetTierBandsForRuleArgs,
    ToolArgValidationError,
    validate_tool_args,
)
from app.core.exceptions import (
    InvalidAsOfDateError,
    NoProjectionExistsError,
    OrderNotFoundError,
    ToolLoopExhaustedError,
)
from app.repositories.explanation_repository import ExplanationRepository
from app.repositories.fine_rule_repository import FineRuleRepository
from app.repositories.master_data_repository import MasterDataRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.projection_repository import ProjectionRepository

# 3 optional tool-calling rounds + 1 forced structured-output round.
MAX_TOOL_ROUNDS = 4

# The three exception types below now live in app/core/exceptions.py with
# the rest of the hierarchy; re-exported here so existing
# `from app.services.explanation_service import ...` imports keep working.
__all__ = [
    "MAX_TOOL_ROUNDS",
    "ExplanationService",
    "InvalidAsOfDateError",
    "NoProjectionExistsError",
    "ToolLoopExhaustedError",
]

# What a client is told when the upstream model fails. Static on purpose:
# the underlying SDK exception goes in `AppError.detail`, which is logged
# and never serialized.
_UPSTREAM_FAILURE_MESSAGE = "Explanation generation failed upstream"

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _wrap_data(payload: dict | list) -> str:
    """Wraps retrieved/tool content in a delimited <DATA> block -- never
    spliced into the system prompt, and never treated by the model as an
    instruction. See .claude/skills/llm-agent-patterns/SKILL.md."""
    body = json.dumps(payload, default=_json_default, sort_keys=True)
    return f"<DATA>\n{body}\n</DATA>"


@dataclass
class ExplanationService:
    orders: OrderRepository
    rules: FineRuleRepository
    master_data: MasterDataRepository
    projections: ProjectionRepository
    explanations: ExplanationRepository
    llm: StructuredChatClient

    def explain_order(
        self,
        order_id: str,
        as_of_date: date | None = None,
        force_regenerate: bool = False,
    ) -> ProjectionExplanationOutput:
        # Logged immediately, before any work -- a request that's going to
        # take a while (a real LLM call, possibly several rounds) should
        # never look indistinguishable from a request that never arrived.
        # Without this, the only sign of life was the access-log line at
        # the very end, which for a slow/failing upstream call could be
        # minutes away.
        logger.info("Explanation requested for order_id=%s as_of_date=%s", order_id, as_of_date)

        order = self.orders.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        # No project-wide business-timezone convention exists elsewhere in
        # this codebase (see ProjectionService.run_for_order, which has the
        # same naive `date.today()` gap) -- UTC is the explicit, documented
        # default here rather than the ambient local timezone (ruff DTZ011).
        today = datetime.now(UTC).date()
        as_of_date = as_of_date or today

        history = self.projections.get_history(order_id)
        if not history:
            raise NoProjectionExistsError(
                f"No projections exist yet for order_id={order_id!r} -- nothing to explain."
            )

        # Bound as_of_date to real projection history before doing any
        # other work (cache lookup, context assembly, LLM call) -- see
        # InvalidAsOfDateError's docstring for why this matters.
        earliest_projection_date = min(h["projection_date"] for h in history)
        if as_of_date > today:
            raise InvalidAsOfDateError(f"as_of_date={as_of_date!r} is in the future -- today is {today!r}.")
        if as_of_date < earliest_projection_date:
            raise InvalidAsOfDateError(
                f"as_of_date={as_of_date!r} predates order_id={order_id!r}'s earliest "
                f"projection date, {earliest_projection_date!r}."
            )

        mandatory_context = self._assemble_mandatory_context(order, as_of_date, history)
        context_hash = hashlib.sha256(
            json.dumps(mandatory_context, default=_json_default, sort_keys=True).encode("utf-8")
        ).hexdigest()

        if not force_regenerate:
            cached = self.explanations.get_cached(order_id, as_of_date, PROMPT_VERSION)
            if cached is not None:
                return ProjectionExplanationOutput.model_validate(cached["explanation"])

        output = self._run_tool_loop(order_id, order["retailer_id"], as_of_date, mandatory_context)

        # force_regenerate deliberately overwrites a prior row under the
        # same (order_id, as_of_date, prompt_version) key via `replace`;
        # the normal path stays strictly insert-only via `save` so it
        # fails loudly (IntegrityError) rather than silently overwrite
        # audit history it wasn't asked to touch.
        persist = self.explanations.replace if force_regenerate else self.explanations.save
        persist(
            order_id=order_id,
            as_of_date=as_of_date,
            prompt_version=PROMPT_VERSION,
            context_hash=context_hash,
            model_name=getattr(self.llm, "model_name", "unknown"),
            explanation=output.model_dump(mode="json"),
        )
        return output

    def _assemble_mandatory_context(self, order: dict, as_of_date: date, history: list[dict]) -> dict:
        """Everything every explanation deterministically needs, fetched
        once via plain function calls -- never itself exposed as a
        callable tool, per the skill's node-granularity rule that a step
        that's always required doesn't need the model to decide to run
        it."""
        order_id = order["order_id"]
        stacking_mode = self.master_data.get_stacking_mode(order["retailer_id"])
        rules = self.rules.get_rules_for_retailer(order["retailer_id"])
        return {
            "as_of_date": as_of_date,
            "order": order,
            "stacking_mode": stacking_mode,
            "projection_history": history,
            "fine_rules": [
                {
                    "rule_id": r.rule_id,
                    "violation_type": r.violation_type,
                    "calc_type": r.calc_type.value,
                    "rate": r.rate,
                    "threshold_pct": r.threshold_pct,
                    "cap_amount": r.cap_amount,
                    "tiers": [
                        {"band_min": t.band_min, "band_max": t.band_max, "rate": t.rate}
                        for t in (r.tiers or [])
                    ],
                }
                for r in rules
            ],
            "confirmations": self.orders.list_confirmations(order_id),
            "shipments": self.orders.list_shipments(order_id),
            "demand_exceptions": self.orders.list_demand_exceptions(order_id),
            "production_status_history": self.orders.list_production_status_history(order_id),
        }

    def _run_tool_loop(
        self, order_id: str, retailer_id: str, as_of_date: date, mandatory_context: dict
    ) -> ProjectionExplanationOutput:
        messages: list[dict[str, Any]] = [{"role": "user", "content": _wrap_data(mandatory_context)}]
        optional_tool_rounds = MAX_TOOL_ROUNDS - 1  # the last round is the forced structured call

        # Every provider call in this loop -- not just the final structured
        # one -- must land as ToolLoopExhaustedError (502), never a raw
        # 500. A narrower try/except around only `call_structured` looked
        # complete but wasn't: a transient failure on an *earlier*
        # `call_with_tools` round (rate limit, timeout, a flaky deployment
        # exhausting AzureOpenAIChatClient's own retry budget) propagated
        # straight past every layer here and out through the generic
        # `Exception` handler in app/core/exceptions.py as an
        # unsanitized 500 -- a real, live violation of "every failure mode
        # here is an AppError," caught only by actually running this
        # against a real (slow) Azure deployment, not by any mocked-client
        # unit test. Wrapping the whole loop, not just its last line, is
        # the fix -- not a new exception-handling philosophy, just this
        # method's own stated one actually applied to its own body.
        try:
            for round_num in range(1, optional_tool_rounds + 1):
                logger.info(
                    "order_id=%s as_of_date=%s: calling LLM (optional tool round %s/%s, %s messages so far)",
                    order_id,
                    as_of_date,
                    round_num,
                    optional_tool_rounds,
                    len(messages),
                )
                t0 = time.monotonic()
                turn = self.llm.call_with_tools(SYSTEM_PROMPT, messages, EXPLANATION_TOOL_SCHEMAS)
                logger.info(
                    "order_id=%s as_of_date=%s: round %s/%s returned in %.1fs (%s tool call(s))",
                    order_id,
                    as_of_date,
                    round_num,
                    optional_tool_rounds,
                    time.monotonic() - t0,
                    len(turn.tool_calls),
                )
                if not turn.tool_calls:
                    break
                # `tool_calls` must travel with this assistant message, not
                # just `content` -- the provider needs it to reconstruct a
                # wire-format assistant message that actually carries a
                # `tool_calls` array, or the `tool`-role messages appended
                # below have nothing to point back to and the provider
                # rejects the whole turn (see
                # app/agents/providers/azure_openai.py::_to_lc_messages).
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn.content or "",
                        "tool_calls": [
                            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in turn.tool_calls
                        ],
                    }
                )
                for call in turn.tool_calls:
                    tool_result = self._execute_tool(order_id, retailer_id, call.name, call.arguments)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": _wrap_data(tool_result)}
                    )

            logger.info(
                "order_id=%s as_of_date=%s: calling LLM for final structured output (%s messages)",
                order_id,
                as_of_date,
                len(messages),
            )
            t0 = time.monotonic()
            structured_result = self.llm.call_structured(SYSTEM_PROMPT, messages, ProjectionExplanationOutput)
            logger.info(
                "order_id=%s as_of_date=%s: final structured output returned in %.1fs",
                order_id,
                as_of_date,
                time.monotonic() - t0,
            )
        except Exception as exc:
            # `{exc}` is the raw Azure OpenAI SDK error -- it can embed
            # vendor internals and request detail, so it goes in `detail`
            # (logged, never serialized), not in the client-facing message.
            raise ToolLoopExhaustedError(
                _UPSTREAM_FAILURE_MESSAGE,
                detail=(
                    f"Model did not return valid structured output within {MAX_TOOL_ROUNDS} rounds "
                    f"for order_id={order_id!r}, as_of_date={as_of_date!r}: {exc}"
                ),
            ) from exc

        if not isinstance(structured_result, ProjectionExplanationOutput):
            # Defensive, not just a type-checker satisfier: a provider or
            # test fake that ignores `output_schema` and returns some
            # other BaseModel should fail loudly here, not downstream.
            raise ToolLoopExhaustedError(
                _UPSTREAM_FAILURE_MESSAGE,
                detail=(
                    f"Model returned {type(structured_result).__name__}, not "
                    f"ProjectionExplanationOutput, for order_id={order_id!r}, "
                    f"as_of_date={as_of_date!r}."
                ),
            )
        return structured_result

    def _execute_tool(
        self, order_id: str, retailer_id: str, name: str, arguments: dict[str, Any]
    ) -> dict | list:
        validated = validate_tool_args(name, arguments)

        if name == "get_carrier_reliability_detail":
            assert isinstance(validated, GetCarrierReliabilityDetailArgs)
            carriers = self.master_data.list_carriers()
            match = next((c for c in carriers if c["carrier_id"] == validated.carrier_id), None)
            return match or {"carrier_id": validated.carrier_id, "found": False}

        if name == "get_actual_fines_for_order":
            # `order_id` here is always the enclosing order under
            # explanation, passed down from `explain_order` -- never a
            # value taken from `validated`/the model's arguments. See
            # GetActualFinesForOrderArgs's docstring: this tool has no
            # model-supplied order_id at all, so there is no argument to
            # trust or reject in the first place.
            assert isinstance(validated, GetActualFinesForOrderArgs)
            return self.orders.list_actual_fines(order_id)

        if name == "get_tier_bands_for_rule":
            # `retailer_id` here is always the enclosing order's actual
            # retailer, passed down from `explain_order` -- never derived
            # from the model-supplied `rule_id`. Resolving the rule via a
            # system-wide `list_rules()` scan (no retailer filter) let a
            # hallucinating or adversarial model pull another retailer's
            # fine-rule structure -- rate, tier bands, both sensitive
            # rate-card data -- into an explanation for an unrelated
            # order, just by supplying that retailer's rule_id. Scoping
            # the lookup to `get_rules_for_retailer(retailer_id)` up
            # front means a rule_id that's genuinely invalid and one that
            # belongs to a different retailer both fall through to the
            # same "not found" result below -- this tool doesn't leak
            # which rule_ids exist for other retailers either.
            assert isinstance(validated, GetTierBandsForRuleArgs)
            retailer_rules = self.rules.get_rules_for_retailer(retailer_id)
            rule = next((r for r in retailer_rules if r.rule_id == validated.rule_id), None)
            if rule is None:
                return {"rule_id": validated.rule_id, "found": False}
            return {
                "rule_id": rule.rule_id,
                "calc_type": rule.calc_type.value,
                "tiers": [
                    {"band_min": t.band_min, "band_max": t.band_max, "rate": t.rate}
                    for t in (rule.tiers or [])
                ],
            }

        raise ToolArgValidationError(f"Unknown tool: {name!r}")
