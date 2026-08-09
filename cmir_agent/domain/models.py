from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class CMIRStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    PENDING_HUMAN_ACTION = "pending_human_action"
    APPROVED = "approved"
    REJECTED = "rejected"


MANDATORY_FIELDS: Tuple[str, ...] = (
    "sender_type",
    "customer_identity",
    "material_identity",
    "intent_phrase",
    "existing_cmir_ref",
    "brand",
    "site",
)


class CMIR(BaseModel):
    sender_type: str = ""
    customer_identity: str = ""
    material_identity: str = ""
    intent_phrase: str = ""
    existing_cmir_ref: str = ""
    brand: str = ""
    site: str = ""
    target_grd_code: str = ""
    target_customer_material_ref: str = ""
    effective_date: str = ""
    reason: str = ""
    status: str = ""
    missing_fields: List[str] = Field(default_factory=list)


@dataclass(frozen=True)
class EmailMessage:
    """Normalized representation of an inbound CMIR email."""

    imap_id: str
    sender: str
    subject: str
    body: str
    source_message_id: Optional[str] = None
    mark_read: bool = True


@dataclass(frozen=True)
class WorkflowThread:
    """UI-facing workflow identity for one source email / CMIR candidate."""

    thread_id: str
    agent_run_id: int
    batch_id: str
    email_id: str
    source_message_id: Optional[str]
    sender: str
    subject: str
    status: str = "running"
    current_node: Optional[str] = None
    stage: str = "INGESTING"
    cmir_status: Optional[str] = None
    latest_snapshot: Dict[str, Any] = field(default_factory=dict)
    pending_action_id: Optional[int] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class PendingHumanAction:
    """Open human-review interrupt tied to exactly one workflow thread."""

    agent_run_id: int
    batch_id: str
    thread_id: str
    email_id: str
    interrupt_type: str
    payload: Dict[str, Any]
    state_snapshot: Dict[str, Any]
    status: str = "open"
