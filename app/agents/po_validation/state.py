from __future__ import annotations

from typing import Any, Literal, TypedDict
from uuid import UUID


class POGraphState(TypedDict, total=False):
    po_line_id: str
    po_line: dict[str, Any]  # PoLine, as a dict (po_number, customer_id, plant, order_quantity, ...)
    batch_id: str
    run_id: UUID
    thread_id: str  # internal checkpoint key; only promoted to a reviewer-facing
    # workflow_threads.thread_id by the service on first interrupt
    sap_material_number: str | None
    cmir_match_found: bool
    manual_entry_description: str
    material: dict[str, Any]  # matched material_master row, as a dict
    quantity_sufficient: bool
    decision: Literal["use_substitute", "proceed_anyway", "mark_stale"] | None
    error: dict[str, Any] | None
