from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict


class POGraphState(TypedDict, total=False):
    po_line_id: str
    po_line: Dict[str, Any]     # PoLine, as a dict (po_number, customer_id, plant, order_quantity, ...)
    batch_id: str
    run_id: int
    thread_id: str              # internal checkpoint key; only promoted to a reviewer-facing
                                 # workflow_threads.thread_id by the service on first interrupt
    sap_material_number: Optional[str]
    cmir_match_found: bool
    manual_entry_description: str
    material: Dict[str, Any]    # matched material_master row, as a dict
    quantity_sufficient: bool
    decision: Optional[str]     # "use_substitute" | "proceed_anyway" | "mark_stale"
    error: Optional[Dict[str, Any]]
