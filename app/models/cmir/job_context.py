"""CMIR-specific extension tables for job run and job item context."""

from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    CMIR_SCHEMA,
    JSONB_OR_JSON,
    PROCESS_SCHEMA,
    UUID_PK,
    Base,
    TimestampMixin,
)


class CmirJobRunContext(Base, TimestampMixin):
    """Extends `process.job_run` with CMIR batch-ingest metadata."""

    __tablename__ = "cmir_job_run_context"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    job_run_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_run.id"), primary_key=True
    )
    source_type: Mapped[str] = mapped_column(String(50))
    external_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    service_bus_topic: Mapped[str | None] = mapped_column(String(200), nullable=True)
    service_bus_subscription: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)


class CmirJobItemContext(Base, TimestampMixin):
    """Extends `process.job_item` with the email or PO line a work item covers.

    Exactly one of `email_event_id` or `purchase_order_line_id` is set. The
    `CHECK (num_nonnulls(...) = 1)` enforcing that lives in raw migration DDL
    only, because `num_nonnulls` does not exist on SQLite and would break
    `Base.metadata.create_all()` for the test suite.
    """

    __tablename__ = "cmir_job_item_context"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    job_item_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_item.id"), primary_key=True
    )
    email_event_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_event.id"), nullable=True
    )
    purchase_order_line_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("purchase_order_line.id"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)
