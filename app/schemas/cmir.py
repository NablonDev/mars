from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Internal graph-state / value-object shapes (formerly domain/models.py).
# Framework-free except CMIR itself, which is a Pydantic model used as the
# in-memory/graph-state CMIR representation, not an API contract -- it flows
# through LangGraph state and gets reconstructed via CMIR(**dict) throughout
# app/agents/cmir/nodes.py and app/services/cmir_run_service.py.
# ---------------------------------------------------------------------------


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
    "target_customer_material_ref",
)

# Every CMIR business-content field -- i.e. every field a reviewer can edit and every
# field the SCD2 merge (app.services.cmir_merge) considers. Deliberately excludes
# `status` and `missing_fields`, which are derived by CMIRValidator from this
# content, not part of it.
CMIR_CONTENT_FIELDS: Tuple[str, ...] = MANDATORY_FIELDS + (
    "target_grd_code",
    "effective_date",
    "reason",
)


class CMIR(BaseModel):
    sender_type: str = ""
    customer_identity: str = ""
    material_identity: str = Field(
        "",
        description=(
            "Internal/generic material identity mentioned in the email, not necessarily "
            "the customer's own material code."
        ),
    )
    intent_phrase: str = ""
    existing_cmir_ref: str = ""
    brand: str = ""
    site: str = ""
    target_grd_code: str = ""
    target_customer_material_ref: str = Field(
        "",
        description=(
            "The customer's own material number/reference as written in their email -- "
            "e.g. a line labeled 'Customer Material', 'Cust Mat No', 'Customer Material "
            "Code', or similar."
        ),
    )
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
    """UI-facing workflow identity for one source email / CMIR candidate, or one PO line."""

    thread_id: str
    agent_run_id: int
    batch_id: str
    email_id: Optional[str]
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
    po_line_id: Optional[str] = None


@dataclass(frozen=True)
class PendingHumanAction:
    """Open human-review interrupt tied to exactly one workflow thread."""

    agent_run_id: int
    batch_id: str
    thread_id: str
    email_id: Optional[str]
    interrupt_type: str
    payload: Dict[str, Any]
    state_snapshot: Dict[str, Any]
    status: str = "open"
    po_line_id: Optional[str] = None


# ---------------------------------------------------------------------------
# API request/response DTOs (formerly schemas.py).
# ---------------------------------------------------------------------------


class IngestFilters(BaseModel):
    subject_contains: Optional[str] = None
    unread_only: bool = True


class IngestEmailsRequest(BaseModel):
    max_workers: int = Field(default=4, ge=1)
    source: str = "gmail"
    filters: IngestFilters = Field(default_factory=IngestFilters)


class RunThreadSummary(BaseModel):
    batch_id: Optional[str] = None
    agent_run_id: int
    thread_id: str
    email_id: Optional[str] = None
    po_line_id: Optional[str] = None
    source_message_id: Optional[str] = None
    sender: Optional[str] = None
    subject: Optional[str] = None
    stage: str
    status: str
    current_node: Optional[str] = None
    pending_action_id: Optional[int] = None
    updated_at: Optional[str] = None


class IngestEmailsResponse(BaseModel):
    batch_id: str
    status: str
    total_threads: int
    threads: List[RunThreadSummary]


class ProcessQueuedEmailRequest(BaseModel):
    batch_id: str
    email_id: str
    queue_message_id: str
    email: Dict[str, Any]


class ThreadQueueItem(BaseModel):
    batch_id: Optional[str] = None
    agent_run_id: int
    thread_id: str
    email_id: str
    source_message_id: Optional[str] = None
    sender: Optional[str] = None
    subject: Optional[str] = None
    stage: str
    status: str
    current_node: Optional[str] = None
    pending_action_id: Optional[int] = None
    updated_at: str


class BatchQueueItem(BaseModel):
    batch_id: str
    status: str
    total_threads: int
    waiting_threads: int
    completed_threads: int
    failed_threads: int
    started_at: str
    updated_at: str


class RunsResponse(BaseModel):
    items: List[Dict[str, Any]]
    next_cursor: Optional[str] = None


class ThreadStageResponse(RunThreadSummary):
    pass


class SnapshotEmail(BaseModel):
    email_id: str
    sender: Optional[str] = None
    subject: Optional[str] = None
    source_message_id: Optional[str] = None


class SnapshotHistoryItem(BaseModel):
    actor: str
    action_type: Optional[str] = None
    field_changes: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class ThreadSnapshotResponse(BaseModel):
    batch_id: Optional[str] = None
    agent_run_id: int
    thread_id: str
    email: SnapshotEmail
    stage: str
    editable_fields: List[str]
    cmir: Dict[str, Any]
    # Active cmir_records row this draft was merged against (None for a create) and
    # the resulting field-level diff -- see app/services/cmir_merge.py. Not enforced
    # at runtime (the route uses response_model=None, see app/api/v1/cmir.py), but
    # kept accurate as the documented CMIR-side shape.
    existing_cmir: Optional[Dict[str, Any]] = None
    diff: Dict[str, Any] = Field(default_factory=dict)
    history: List[SnapshotHistoryItem]
    updated_at: str


class FieldsRequest(BaseModel):
    actor: str
    fields: Dict[str, Any]
    expected_updated_at: str


class DecisionRequest(BaseModel):
    actor: str
    decision: Literal["approve", "reject"]
    expected_updated_at: str
    reason: str = ""


class UpdateDraftResponse(BaseModel):
    batch_id: Optional[str] = None
    agent_run_id: int
    thread_id: str
    stage: str
    status: str
    pending_action_id: int
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
