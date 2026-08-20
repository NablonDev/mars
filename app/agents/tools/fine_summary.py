"""Tool definitions for fine-summary generation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field


class GetCarrierReliabilityDetailInput(BaseModel):
    carrier_id: str = Field(description="The carrier's business id.")


class GetActualFinesForOrderInput(BaseModel):
    pass


class GetTierBandsForRuleInput(BaseModel):
    rule_id: str = Field(description="The fine rule's business id.")


def build_fine_summary_tools(
    *,
    carrier_reliability: Callable[[str], dict[str, Any]],
    actual_fines: Callable[[], list[dict[str, Any]]],
    tier_bands: Callable[[str], dict[str, Any]],
    order_status: str,
) -> list[BaseTool]:
    """Build tools scoped to the current order."""

    @tool(args_schema=GetCarrierReliabilityDetailInput)
    def get_carrier_reliability_detail(carrier_id: str) -> dict[str, Any]:
        """Look up a carrier's historical reliability details."""
        return carrier_reliability(carrier_id)

    @tool(args_schema=GetActualFinesForOrderInput)
    def get_actual_fines_for_order() -> list[dict[str, Any]] | dict[str, Any]:
        """Look up actual fines recorded for the current order. Only
        meaningful once the order has been DELIVERED."""
        if order_status != "DELIVERED":
            return {"available": False, "reason": f"order_status is {order_status!r}, not DELIVERED"}

        return actual_fines()

    @tool(args_schema=GetTierBandsForRuleInput)
    def get_tier_bands_for_rule(rule_id: str) -> dict[str, Any]:
        """Look up tier bands for a fine rule."""
        return tier_bands(rule_id)

    return [
        get_carrier_reliability_detail,
        get_actual_fines_for_order,
        get_tier_bands_for_rule,
    ]
