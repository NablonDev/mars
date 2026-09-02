"""Penalties-specific extension tables for `process.job_run`/
`process.job_item`. See `app/models/cmir/job_context.py` for why these
stay domain-owned rather than folding into `process`, why the Python class
names are prefixed (`PenaltyJobRunContext`/`PenaltyJobItemContext`), and
why -- as a forced deviation from the approved plan's exact file list --
the table names are prefixed too (`penalty_job_run_context`/
`penalty_job_item_context`, not the bare `job_run_context`/
`job_item_context` the plan specifies): the plan's own section 1.1 SQLite
schema-translation rule collapses both this schema's and `cmir`'s
identically-named tables into one unqualified SQLite namespace, and
`Base.metadata.create_all()` cannot create two tables with the same name."""

from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    JSONB_OR_JSON,
    PENALTIES_SCHEMA,
    PROCESS_SCHEMA,
    UUID_PK,
    Base,
    TimestampMixin,
)


class PenaltyJobRunContext(Base, TimestampMixin):
    """Extends `process.job_run` with penalty-projection batch metadata."""

    __tablename__ = "penalty_job_run_context"
    __table_args__ = ({"schema": PENALTIES_SCHEMA},)

    job_run_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_run.id"), primary_key=True
    )
    projection_date: Mapped[date] = mapped_column(Date)
    stacking_mode_override: Mapped[str | None] = mapped_column(String(30), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)


class PenaltyJobItemContext(Base, TimestampMixin):
    """Extends `process.job_item` with which PO and projection date this
    work item is about."""

    __tablename__ = "penalty_job_item_context"
    __table_args__ = ({"schema": PENALTIES_SCHEMA},)

    job_item_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_item.id"), primary_key=True
    )
    purchase_order_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order.id"))
    projection_date: Mapped[date] = mapped_column(Date)
    task_type: Mapped[str] = mapped_column(String(100))
    stacking_mode_override: Mapped[str | None] = mapped_column(String(30), nullable=True)
    force_regenerate_summary: Mapped[bool] = mapped_column(Boolean, default=False)
