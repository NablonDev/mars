"""Database models for batch job runs and work items."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7
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

    Run status is derived from its job items rather than stored separately
    to avoid a shared row becoming a concurrency bottleneck.
    """

    __tablename__ = "job_run"
    __table_args__ = (
        CheckConstraint(
            _check_in_sql("run_type", JobRunType),
            name="ck_job_run_run_type",
        ),
        {"schema": FINES_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    run_type: Mapped[str] = mapped_column(String(30))
    projection_date: Mapped[date] = mapped_column(Date)
    stacking_mode_override: Mapped[str | None] = mapped_column(String(30), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requested_item_count: Mapped[int] = mapped_column(Integer, default=0)


class JobItem(Base, TimestampMixin):
    """A unit of work for one order, projection date, and task type.

    Retryable failures return to PENDING with a future available_at;
    terminal failures transition to DEAD. There is no resting FAILED state.
    """

    __tablename__ = "job_item"
    __table_args__ = (
        CheckConstraint(
            _check_in_sql("status", JobItemStatus),
            name="ck_job_item_status",
        ),
        CheckConstraint(
            _check_in_sql("task_type", JobTaskType),
            name="ck_job_item_task_type",
        ),
        Index("ix_job_item_claimable", "status", "available_at"),
        Index("ix_job_item_job_run_id", "job_run_id"),
        {"schema": FINES_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    job_run_id: Mapped[UUID] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.job_run.id"))
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    projection_date: Mapped[date] = mapped_column(Date)
    task_type: Mapped[str] = mapped_column(String(64))
    stacking_mode_override: Mapped[str | None] = mapped_column(String(30), nullable=True)
    force_regenerate_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default=JobItemStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
