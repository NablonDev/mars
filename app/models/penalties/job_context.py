"""Penalties-specific extension tables for process.job_run and process.job_item."""

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
    """Extends `process.job_item` with the PO and projection date a work item covers.

    A `DISPUTE_SUMMARY_REGEN` item carries its dispute id in
    `process.job_item.metadata_json` rather than in a column here, since a PO
    can have several concurrent disputes. Such an item still populates
    `purchase_order_id` and `projection_date` for consistency, but no worker
    keys a lookup on them.
    """

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
