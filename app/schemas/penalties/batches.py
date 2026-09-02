"""API schemas for the generic `process.job_run`/`job_item` batch
trigger/status endpoints. CMIR email ingestion is triggered exclusively via
the dedicated `POST /cmir/email-events` route (`app/api/v1/cmir.py`), not
through `/job-runs` -- these `job_type`s cover the `penalties` domain only.

Was `app/schemas/batches.py`, rewritten against the domain-agnostic
`process.job_run`/`job_item` (Phase 1/2): no `order_id` column any more
(replaced by a generic `dedupe_key`); `task_type` -> `item_type`
(`JobItem.item_type` is the real discriminator -- see that model's
docstring)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, Field, model_validator

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


class PenaltyMitigationBatchRequest(BaseModel):
    """`job_type=PENALTY_MITIGATION_BATCH` -- computes and persists ranked
    mitigation options for every OPEN purchase order that already has a
    persisted penalty projection. Maps internally to
    `JobTaskType.MITIGATION_RUN`; see `app/api/v1/job_runs.py`.

    No `projection_date`/`stacking_mode_override` field, unlike
    `PenaltyProjectionBatchRequest` above: `MitigationService.
    run_for_purchase_order` (`app/services/penalties/mitigation/service.py`)
    takes no stacking-mode override of its own -- it always resolves the
    retailer's *current* stacking mode -- and each purchase order's options
    are computed against *its own* latest persisted projection date, which
    can differ per purchase order, so there is no single shared date to
    parameterize a batch run with. A purchase order with no projection at
    all is skipped, not errored -- the same eligibility check
    `run_for_purchase_order` itself performs for a single PO/date (a
    `BusinessRuleError(code="NO_PROJECTION_EXISTS")` there becomes a silent
    skip here)."""

    job_type: Literal["PENALTY_MITIGATION_BATCH"] = "PENALTY_MITIGATION_BATCH"


class PenaltyFullRunScope(BaseModel):
    """Which purchase orders `job_type=PENALTY_FULL_RUN_BATCH` applies to --
    either every purchase order matching `purchase_order_status` (default
    `"OPEN"`, mirroring every other batch's own default) or an explicit
    `purchase_order_ids` list. At most one may be *meaningfully* set: an
    omitted `purchase_order_status` defaults to `"OPEN"` silently and is not
    a conflict on its own, but explicitly supplying both is rejected --
    there's no well-defined "AND" semantics a caller would expect here."""

    purchase_order_status: str | None = "OPEN"
    purchase_order_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def _validate_mutually_exclusive(self) -> PenaltyFullRunScope:
        if self.purchase_order_ids is not None and "purchase_order_status" in self.model_fields_set:
            raise ValueError(
                "Provide at most one of `purchase_order_status` or `purchase_order_ids` -- not both."
            )
        return self


class PenaltyFullRunBatchRequest(BaseModel):
    """`job_type=PENALTY_FULL_RUN_BATCH` -- runs the requested subset of
    projection -> projection_summary -> mitigation -> mitigation_summary
    (any subset/order in `steps`, always executed in that fixed dependency
    order) for every purchase order matching `scope`. Maps internally to
    `JobTaskType.PENALTY_FULL_RUN`; see `app/api/v1/job_runs.py` and the
    worker, `app/workers/penalty_full_run.py`.

    Eligibility mirrors `PenaltyMitigationBatchRequest`'s own skip rule
    exactly when `"projection"` is not itself in `steps`: a purchase order
    with no existing persisted projection is skipped, not failed. When
    `"projection"` IS requested, every matching purchase order is eligible
    (the run will produce one)."""

    job_type: Literal["PENALTY_FULL_RUN_BATCH"] = "PENALTY_FULL_RUN_BATCH"
    steps: Annotated[
        list[Literal["projection", "projection_summary", "mitigation", "mitigation_summary"]],
        Field(min_length=1),
    ]
    scope: PenaltyFullRunScope = Field(default_factory=PenaltyFullRunScope)
    projection_date: date | None = None


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
    PenaltyProjectionBatchRequest | PenaltyMitigationBatchRequest | PenaltyFullRunBatchRequest,
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
