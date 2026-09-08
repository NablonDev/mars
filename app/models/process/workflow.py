"""Reviewer-facing HITL workflow thread and its polymorphic subject."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    CMIR_SCHEMA,
    JSONB_OR_JSON,
    PROCESS_SCHEMA,
    UUID_PK,
    Base,
    TimestampMixin,
    generate_uuid7,
)


class WorkflowThread(Base, TimestampMixin):
    """UI-facing workflow thread.

    Created only when a job item's first human interrupt fires, so not every
    job item has one.
    """

    __tablename__ = "workflow_thread"
    __table_args__ = ({"schema": PROCESS_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    job_item_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_item.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), default="running")
    stage: Mapped[str] = mapped_column(String(100))
    current_node: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)


class WorkflowThreadSubject(Base, TimestampMixin):
    """1:1 subtype/extension of WorkflowThread with two nullable typed FKs."""

    __tablename__ = "workflow_thread_subject"
    __table_args__ = ({"schema": PROCESS_SCHEMA},)

    workflow_thread_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.workflow_thread.id"), primary_key=True
    )
    email_event_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_event.id"), nullable=True
    )
    purchase_order_line_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("purchase_order_line.id"), nullable=True
    )
