"""Database models for batch job runs and work items -- domain-generic,
shared by both `cmir`/`po_validation` and `penalties`. Domain-specific
columns (order/PO id, projection date, email/PO-line id, ...) live in each
domain's own `job_run_context`/`job_item_context` extension table
(`app/models/cmir/job_context.py`, `app/models/penalties/job_context.py`),
not here."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSONB_OR_JSON, PROCESS_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7
from app.models.enums import JobItemStatus, JobRunType, JobTaskType


def _check_in_sql(
    column: str,
    enum_cls: type[JobRunType] | type[JobItemStatus] | type[JobTaskType],
) -> str:
    """Build a deterministic SQL IN expression from a StrEnum."""
    # Sort values to keep generated DDL deterministic across runs.
    values = ", ".join(f"'{value.value}'" for value in sorted(enum_cls))
    return f"{column} IN ({values})"


class JobRun(Base, TimestampMixin):
    """A single scheduled, manual, or on-demand batch run.

    Deliberately has no status column: concurrent workers update JobItem
    rows continuously while a run is in flight, and a status column here
    would mean every one of them also has to lock this single shared row,
    a hot-lock bottleneck for no real benefit. A run's status is derived at
    read time by grouping job_item rows by job_run_id/status. This
    diverges from docs/redesigned-schema.md's process.job_run table (which
    lists a `status` column) -- that doc was drafted without this
    codebase's context; this file preserves the existing, deliberate
    design instead. Do not add a status column back without re-deriving
    why this was avoided.
    """

    __tablename__ = "job_run"
    __table_args__ = (
        CheckConstraint(
            _check_in_sql("trigger_type", JobRunType),
            name="ck_job_run_trigger_type",
        ),
        {"schema": PROCESS_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    job_type: Mapped[str] = mapped_column(String(100))
    trigger_type: Mapped[str] = mapped_column(String(50))
    requested_item_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)


class JobItem(Base, TimestampMixin):
    """A generic unit of work claimed by a worker.

    Retryable failures return to PENDING with a future available_at;
    terminal failures transition to DEAD. There is no resting FAILED state.

    In-flight dedupe (`uq_job_item_inflight`, migration-only raw DDL --
    see the `process` schema revision) is restored on top of a generic
    `dedupe_key` column rather than the old domain-shaped business key
    (order_id, projection_date, task_type): those columns moved to each
    domain's own `job_item_context` extension table (see
    `app/models/penalties/job_context.py`, `app/models/cmir/job_context.py`),
    which cannot itself express a `WHERE status IN (...)` predicate since
    `status` lives here, on `process.job_item`, not on the extension
    table. `dedupe_key` is queue infrastructure, not a domain column --
    the domain layer computes and passes the string at enqueue time (e.g.
    ``f"{purchase_order_id}:{projection_date}"`` for penalties, the email
    event id for CMIR); this table only stores and indexes it. Rows that
    don't need dedup (or a domain that hasn't wired it up yet) leave it
    NULL, which the partial index's `dedupe_key IS NOT NULL` clause
    excludes entirely -- unlike leaving `dedupe_key` empty-string, which
    would collide. Corrected from an earlier version of this docstring
    that (incorrectly) claimed `JobQueueRepository` already had an
    application-level fallback making this DB constraint non-essential:
    the repository's own `enqueue_many()` calls
    `on_conflict_do_nothing(index_elements=..., index_where=...)`, which
    requires a matching index to exist on Postgres or raises at runtime --
    the partial index is the primary defense there, not a backstop.

    Note: `penalties.penalty_job_item_context.task_type` currently
    duplicates this table's `item_type` with nothing keeping the two in
    sync. `item_type` (here, on `process.job_item`) is the real
    discriminator used by `uq_job_item_inflight` and should be treated as
    the source of truth; `task_type` is not removed in this phase (that's
    a domain-context-table shape decision out of scope here), but any new
    code should read `item_type`, not `task_type`.
    """

    __tablename__ = "job_item"
    __table_args__ = (
        CheckConstraint(
            _check_in_sql("status", JobItemStatus),
            name="ck_job_item_status",
        ),
        CheckConstraint(
            _check_in_sql("item_type", JobTaskType),
            name="ck_job_item_item_type",
        ),
        Index("ix_job_item_claimable", "status", "available_at"),
        Index("ix_job_item_job_run_id", "job_run_id"),
        {"schema": PROCESS_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    job_run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_run.id"))
    item_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default=JobItemStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    # Queue infrastructure, not a domain column -- see the class docstring.
    # Populated by the domain layer at enqueue time (Phase 2/3); NULL rows
    # are excluded from uq_job_item_inflight entirely.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)
