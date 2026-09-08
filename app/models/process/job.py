"""Batch job runs and work items, shared by cmir/po_validation and penalties domains."""

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
    """Build a deterministic SQL IN expression from the enum's own members, never caller input."""
    values = ", ".join(f"'{value.value}'" for value in sorted(enum_cls))
    return f"{column} IN ({values})"


class JobRun(Base, TimestampMixin):
    """A single scheduled, manual, or on-demand batch run."""

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
    """A generic unit of work claimed by a worker."""

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
    # Queue infrastructure, not a domain column; see the class docstring.
    # Populated by the domain layer at enqueue time. NULL rows are excluded
    # from uq_job_item_inflight entirely.
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
