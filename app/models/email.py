from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, Base


class EmailEventORM(Base):
    """Persisted inbound email event."""

    __tablename__ = "email_events"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    sender: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    raw_content: Mapped[str] = mapped_column(Text)
    source_message_id: Mapped[str | None] = mapped_column(Text)
    source_imap_id: Mapped[str | None] = mapped_column(Text)
    extracted_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSON)
    status: Mapped[str | None] = mapped_column(String)
    queue_status: Mapped[str] = mapped_column(Text, default="new")
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queue_message_id: Mapped[str | None] = mapped_column(Text)
    queue_delivery_count: Mapped[int] = mapped_column(Integer, default=0)
    queue_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailActionLogORM(Base):
    """Audit log for email-level workflow events."""

    __tablename__ = "email_action_log"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
