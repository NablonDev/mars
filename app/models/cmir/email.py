"""Inbound email events and their audit log."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, JSONB_OR_JSON, UUID_PK, Base, TimestampMixin, generate_uuid7


class EmailEvent(Base, TimestampMixin):
    """One row per inbound email, doubling as the Service Bus queue's work item."""

    __tablename__ = "email_event"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    sender: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_imap_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extracted_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queue_status: Mapped[str] = mapped_column(String(30), default="new")
    queue_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    queue_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    queue_delivery_count: Mapped[int] = mapped_column(Integer, default=0)


class EmailActionLog(Base, TimestampMixin):
    """Append-only audit log for email-level workflow events."""

    __tablename__ = "email_action_log"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    email_event_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_event.id"))
    action: Mapped[str] = mapped_column(String(100))
    actor: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB_OR_JSON)
