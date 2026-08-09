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
