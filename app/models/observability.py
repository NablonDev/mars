from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, JSONB_OR_JSON, UUID_PK, Base, generate_uuid7


class AgentRunORM(Base):
    """One per-email agent execution grouped by batch_id."""

    __tablename__ = "agent_runs"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    batch_id: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)
    email_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False))
    po_line_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False))
    status: Mapped[str] = mapped_column(String, default="running")
    current_node: Mapped[str | None] = mapped_column(Text)
    run_type: Mapped[str] = mapped_column(Text, default="email_ingest")
    total_threads: Mapped[int] = mapped_column(Integer, default=0)
    completed_threads: Mapped[int] = mapped_column(Integer, default=0)
    waiting_threads: Mapped[int] = mapped_column(Integer, default=0)
    failed_threads: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class WorkflowThreadORM(Base):
    """UI-facing workflow thread for one source email."""

    __tablename__ = "workflow_threads"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    thread_id: Mapped[str] = mapped_column(Text, unique=True)
    batch_id: Mapped[str] = mapped_column(Text)
    agent_run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    email_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey(f"{CMIR_SCHEMA}.email_events.id")
    )
    po_line_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey(f"{CMIR_SCHEMA}.po_lines.id")
    )
    source_message_id: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="running")
    current_node: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text, default="INGESTING")
    cmir_status: Mapped[str | None] = mapped_column(Text)
    latest_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    pending_action_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class PendingHumanActionORM(Base):
    """Open or completed human-review interrupt."""

    __tablename__ = "pending_human_actions"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    batch_id: Mapped[str] = mapped_column(Text)
    agent_run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    thread_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{CMIR_SCHEMA}.workflow_threads.thread_id"))
    email_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey(f"{CMIR_SCHEMA}.email_events.id")
    )
    po_line_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey(f"{CMIR_SCHEMA}.po_lines.id")
    )
    interrupt_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB_OR_JSON)
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(Text, default="open")
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON)
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentTraceORM(Base):
    """One row per LangGraph node execution."""

    __tablename__ = "agent_traces"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    batch_id: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)
    node_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class HITLActionORM(Base):
    """Audit trail of human answers and draft edits."""

    __tablename__ = "hitl_actions"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    batch_id: Mapped[str | None] = mapped_column(Text)
    email_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False))
    interrupt_type: Mapped[str] = mapped_column(Text)
    question: Mapped[dict[str, Any]] = mapped_column(JSON)
    answer: Mapped[dict[str, Any]] = mapped_column(JSON)
    decision: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    thread_id: Mapped[str | None] = mapped_column(Text)
    action_type: Mapped[str | None] = mapped_column(Text)
    field_changes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    po_line_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False))
