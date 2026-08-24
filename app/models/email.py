from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, JSONB_OR_JSON, UUID_PK, Base, TimestampMixin, generate_uuid7


class EmailEventORM(Base, TimestampMixin):
    """Persisted inbound email event."""

    __tablename__ = "email_events"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    sender: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(Text)
    source_imap_id: Mapped[str | None] = mapped_column(String(100))
    extracted_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSONB_OR_JSON)
    status: Mapped[str | None] = mapped_column(String(64))
    queue_status: Mapped[str] = mapped_column(String(30), default="new")
    queue_message_id: Mapped[str | None] = mapped_column(String(200))
    queue_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queue_delivery_count: Mapped[int] = mapped_column(Integer, default=0)


class EmailActionLogORM(Base, TimestampMixin):
    """Audit log for email-level workflow events."""

    __tablename__ = "email_action_logs"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    email_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_events.id"))
    action: Mapped[str] = mapped_column(String(100))
    actor: Mapped[str] = mapped_column(String(50))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB_OR_JSON)
