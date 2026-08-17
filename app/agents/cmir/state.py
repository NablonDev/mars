from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict


class GraphState(TypedDict, total=False):
    email: Dict[str, Any]      # EmailMessage, as a dict
    email_id: Any
    batch_id: str
    run_id: int
    thread_id: str
    cmir: Dict[str, Any]       # CMIR, as a dict
    decision: Optional[str]    # "approve" | "reject"
    decision_reason: str
    existing_cmir: Optional[Dict[str, Any]]  # current cmir_records row for this entity, or None
    cmir_diff: Dict[str, Any]                # field-level diff from merge_with_active
    cmir_version_token: Optional[int]        # existing_cmir["id"] at diff time, for supersede_and_insert
    cmir_write_result: str                   # "committed" | "conflict", set by persist_cmir
