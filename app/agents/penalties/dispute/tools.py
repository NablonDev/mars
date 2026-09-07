"""Tool definitions for dispute-summary generation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel


class GetRuleDetailInput(BaseModel):
    pass


class GetFactsUsedInput(BaseModel):
    pass


class GetPriorDisputeHistoryInput(BaseModel):
    pass


def build_dispute_summary_tools(
    *,
    rule_detail: Callable[[], dict[str, Any]],
    facts_used: Callable[[], dict[str, Any]],
    prior_dispute_history: Callable[[], list[dict[str, Any]]],
) -> list[BaseTool]:
    """Build tools scoped to the current dispute."""

    @tool(args_schema=GetRuleDetailInput)
    def get_rule_detail() -> dict[str, Any]:
        """Look up the full penalty rule (including tier bands, if TIERED)
        matched by the deterministic engine for this dispute."""
        return rule_detail()

    @tool(args_schema=GetFactsUsedInput)
    def get_facts_used() -> dict[str, Any]:
        """Look up the real, final post-delivery facts (delivered quantity,
        actual delivery date, ...) the deterministic engine actually fed
        into the calculation for this dispute."""
        return facts_used()

    @tool(args_schema=GetPriorDisputeHistoryInput)
    def get_prior_dispute_history_for_purchase_order() -> list[dict[str, Any]]:
        """Look up every other dispute (any status) ever raised against
        this purchase order, oldest first."""
        return prior_dispute_history()

    return [get_rule_detail, get_facts_used, get_prior_dispute_history_for_purchase_order]
