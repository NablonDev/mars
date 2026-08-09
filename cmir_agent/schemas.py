from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


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
    email_id: str
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
