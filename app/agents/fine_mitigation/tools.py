"""Tool definitions for fine-mitigation-summary generation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field


class GetCarrierReliabilityDetailInput(BaseModel):
    carrier_id: str = Field(description="The carrier's business id.")


class GetActualFinesForOrderInput(BaseModel):
    pass


def build_fine_mitigation_summary_tools(
    *,
    carrier_reliability: Callable[[str], dict[str, Any]],
    actual_fines: Callable[[], list[dict[str, Any]]],
    order_status: str,
) -> list[BaseTool]:
    """Build tools scoped to the current order."""

    @tool(args_schema=GetCarrierReliabilityDetailInput)
    def get_carrier_reliability_detail(carrier_id: str) -> dict[str, Any]:
        """Look up a carrier's historical reliability details -- useful
        color when explaining why the FASTER_CARRIER option would (or
        would not) meaningfully reduce delay risk."""
        return carrier_reliability(carrier_id)

    @tool(args_schema=GetActualFinesForOrderInput)
    def get_actual_fines_for_order() -> list[dict[str, Any]] | dict[str, Any]:
        """Look up actual fines recorded for the current order. Only
        meaningful once the order has been DELIVERED."""
        if order_status != "DELIVERED":
            return {"available": False, "reason": f"order_status is {order_status!r}, not DELIVERED"}

        return actual_fines()

    return [
        get_carrier_reliability_detail,
        get_actual_fines_for_order,
    ]
