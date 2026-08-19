"""API schemas for batch trigger and observability endpoints (job_run / job_item)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import JobItemStatus, JobTaskType


class BatchRunRequest(BaseModel):
    """Body for POST /batches/run."""

    projection_date: date | None = None
    stacking_mode_override: Literal["SUM", "MAX"] | None = None


class BatchRunResponse(BaseModel):
    job_run_id: UUID
    requested_item_count: int
    # Backend and pickup behavior at dispatch time; not an ETA.
    dispatch_mode: str
    execution_note: str


class BatchStatusCounts(BaseModel):
    PENDING: int
    RUNNING: int
    SUCCEEDED: int
    DEAD: int


class BatchStatusResponse(BaseModel):
    job_run_id: UUID
    requested_item_count: int
    counts: BatchStatusCounts
    total_items: int
    is_complete: bool


class BatchItemResponse(BaseModel):
    id: UUID
    order_id: str
    projection_date: date
    task_type: JobTaskType
    status: JobItemStatus
    attempt_count: int
    max_attempts: int
    # Expose only a safe category/message; raw error text stays internal.
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class BatchItemListResponse(BaseModel):
    job_run_id: UUID
    items: list[BatchItemResponse]
    limit: int
    offset: int
