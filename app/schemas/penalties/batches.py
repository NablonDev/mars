"""API schemas for the generic `process.job_run`/`job_item` batch
trigger/status endpoints -- shared by `penalties` and, as of the CMIR
follow-up pass (Phase 7b), `cmir`, per the approved plan §5/§6.

Was `app/schemas/batches.py`, rewritten against the domain-agnostic
`process.job_run`/`job_item` (Phase 1/2): no `order_id` column any more
(replaced by a generic `dedupe_key`); `task_type` -> `item_type`
(`JobItem.item_type` is the real discriminator -- see that model's
docstring)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, Field

from app.models.enums import JobItemStatus


class PenaltyProjectionBatchRequest(BaseModel):
    """`job_type=PENALTY_PROJECTION_BATCH` -- runs a penalty projection for
    every OPEN purchase order. Maps internally to `JobTaskType.ORDER_RUN` --
    `app/models/enums.py` is out of scope for this phase, so the wire-level
    vocabulary and the stored `process.job_item.item_type` value
    intentionally differ; see `app/api/v1/job_runs.py`."""

    job_type: Literal["PENALTY_PROJECTION_BATCH"] = "PENALTY_PROJECTION_BATCH"
    projection_date: date | None = None
    stacking_mode_override: Literal["SUM", "MAX"] | None = None


class CmirEmailIngestJobRunRequest(BaseModel):
    """`job_type=CMIR_EMAIL_INGEST` -- Phase 7b addition (approved plan §6):
    fetches unread CMIR emails and enqueues one `process.job_item` per
    email. Maps internally to `JobTaskType.EMAIL_INGEST` (already existed
    per the plan's `job_item` CHECK list; reused, not duplicated) --
    dispatches to `CmirRunService.start_email_ingest`, which does its own
    `process.job_run`/`job_item` bookkeeping directly (no `JobDispatcher`
    wiring needed here, unlike the penalty-projection branch: the actual
    email processing is picked up by the Service Bus consumer / `/internal/
    process-email`, not a generic job-queue worker)."""

    job_type: Literal["CMIR_EMAIL_INGEST"] = "CMIR_EMAIL_INGEST"
    max_workers: int = Field(default=4, ge=1)
    subject_contains: str | None = None
    unread_only: bool = True


def _default_job_type(value: Any) -> Any:
    """`POST /job-runs` with an empty/omitted body historically defaulted to
    `job_type=PENALTY_PROJECTION_BATCH` (Phase 7a, still relied on by
    `tests/unit/api/test_api_batches.py`) -- a Pydantic discriminated union
    needs the tag key present in the raw input to resolve which member
    applies (member-level `job_type` defaults alone don't help it pick), so
    this backfills the tag before discriminator resolution runs. Must sit
    *after* `Field(discriminator=...)` in the `Annotated` chain -- a
    `BeforeValidator` listed before the discriminator metadata does not run
    early enough to affect tag resolution."""
    if isinstance(value, dict) and "job_type" not in value:
        return {**value, "job_type": "PENALTY_PROJECTION_BATCH"}
    return value


JobRunRequest = Annotated[
    PenaltyProjectionBatchRequest | CmirEmailIngestJobRunRequest,
    Field(discriminator="job_type"),
    BeforeValidator(_default_job_type),
]


class JobRunResponse(BaseModel):
    job_run_id: UUID
    requested_item_count: int
    # Backend and pickup behavior at dispatch time; not an ETA.
    dispatch_mode: str
    execution_note: str


class JobRunStatusCounts(BaseModel):
    PENDING: int
    RUNNING: int
    SUCCEEDED: int
    DEAD: int


class JobRunStatusResponse(BaseModel):
    job_run_id: UUID
    requested_item_count: int
    counts: JobRunStatusCounts
    total_items: int
    is_complete: bool


class JobItemResponse(BaseModel):
    id: UUID
    item_type: str
    dedupe_key: str | None = None
    status: JobItemStatus
    attempt_count: int
    max_attempts: int
    # Expose only a safe category/message; raw error text stays internal.
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class JobItemListResponse(BaseModel):
    job_run_id: UUID
    items: list[JobItemResponse]
    limit: int
    offset: int
