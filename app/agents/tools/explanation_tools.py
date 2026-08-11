"""
Optional tool schemas for the projection-explanation tool-calling loop --
model-decided, unlike the mandatory context `ExplanationService` always
pre-fetches. See app/agents/explanation_schema.py's module docstring and
docs/FINE_ENGINE.md for what each of these adds:

  - get_carrier_reliability_detail -- only if the model wants to justify
    the delay-probability carrier multiplier.
  - get_actual_fines_for_order -- only relevant post-delivery. Takes no
    model-supplied order_id: there is exactly one correct order_id per
    call (the order under explanation), so `ExplanationService` always
    scopes this tool to the enclosing order and ignores anything the
    model passes -- a hallucinating or adversarial model cannot use this
    tool to read another order's actual fines.
  - get_tier_bands_for_rule -- only relevant for TIERED rules (none of
    the four current mock rules are TIERED). The model may supply any
    `rule_id`, but `ExplanationService._execute_tool` only ever resolves
    it against `get_rules_for_retailer(<the order under explanation's own
    retailer_id>)`, never a system-wide, unfiltered rule scan -- a
    hallucinating or adversarial model cannot use this tool to pull
    another retailer's rate-card data (rate, tier bands) by supplying
    that retailer's rule_id. A rule_id that's genuinely invalid and one
    that belongs to a different retailer both come back as the same
    "not found" result, so the tool doesn't leak which rule_ids exist
    for other retailers either.

Every tool's arguments are validated against an explicit Pydantic model
before execution, even though the provider also validates -- fail closed
on a malformed or adversarial argument. See
.claude/skills/llm-agent-patterns/SKILL.md.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError


class GetCarrierReliabilityDetailArgs(BaseModel):
    carrier_id: str


class GetActualFinesForOrderArgs(BaseModel):
    """Deliberately no `order_id` field. There is exactly one correct
    order_id for this tool per call -- the order the explanation was
    requested for -- so it is not a model-controlled argument at all.
    `ExplanationService._execute_tool` always uses the enclosing
    order_id, never anything the model supplies; any `order_id` the
    model includes anyway is silently ignored by Pydantic's default
    extra="ignore" behavior rather than rejected, since it's harmless
    once it can't influence which order gets looked up."""


class GetTierBandsForRuleArgs(BaseModel):
    """`rule_id` stays model-supplied -- unlike `order_id` on
    `GetActualFinesForOrderArgs`, there can be more than one rule for the
    order's retailer, so the model legitimately needs to name which one.
    What's not model-controlled is the *retailer scope* the id is
    resolved within: `ExplanationService._execute_tool` always resolves
    `rule_id` against `get_rules_for_retailer(<the enclosing order's own
    retailer_id>)`, never a system-wide, unfiltered scan, so a rule_id
    belonging to a different retailer can't be used to pull that
    retailer's rate-card data into this order's explanation."""

    rule_id: str


_TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    "get_carrier_reliability_detail": GetCarrierReliabilityDetailArgs,
    "get_actual_fines_for_order": GetActualFinesForOrderArgs,
    "get_tier_bands_for_rule": GetTierBandsForRuleArgs,
}

EXPLANATION_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_carrier_reliability_detail",
            "description": (
                "Look up a carrier's historical reliability score, used to "
                "justify the delay-probability carrier multiplier. Call "
                "only if you need to explain why a specific carrier's "
                "reliability affected the delay risk."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "carrier_id": {
                        "type": "string",
                        "description": "The carrier's business id, e.g. CAR-SWIFT.",
                    }
                },
                "required": ["carrier_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_actual_fines_for_order",
            "description": (
                "Look up any actual (post-delivery) fines already recorded "
                "for the order currently being explained. Only relevant "
                "once the order has been delivered -- call only if the "
                "mandatory context suggests the order may already be "
                "delivered or you need to compare the projection against a "
                "recorded real outcome. Takes no arguments: it always looks "
                "up fines for the order under explanation, never a "
                "different order."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tier_bands_for_rule",
            "description": (
                "Look up the tier bands for a TIERED fine rule. Only "
                "relevant when the applicable rule's calc_type is TIERED -- "
                "do not call this for FLAT_FEE, PER_UNIT, or PERCENT_OF_PO "
                "rules."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "The fine rule's business id.",
                    }
                },
                "required": ["rule_id"],
            },
        },
    },
]


class ToolArgValidationError(ValueError):
    """Raised when a tool call's arguments don't match its schema. Fail
    closed even though the provider also validates tool-call arguments."""


def validate_tool_args(tool_name: str, arguments: dict[str, Any]) -> BaseModel:
    model_cls = _TOOL_ARG_MODELS.get(tool_name)
    if model_cls is None:
        raise ToolArgValidationError(f"Unknown tool: {tool_name!r}")
    try:
        return model_cls.model_validate(arguments)
    except ValidationError as exc:
        raise ToolArgValidationError(f"Invalid arguments for tool {tool_name!r}: {exc}") from exc
