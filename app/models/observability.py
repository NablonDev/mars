from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, JSONB_OR_JSON, UUID_PK, Base, generate_uuid7


class AgentRunORM(Base):
    """One per-email agent execution grouped by batch_id."""

    __tablename__ = "agent_runs"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    batch_id: Mapped[str | None] = mapped_column(String(50))
    thread_id: Mapped[str | None] = mapped_column(String(50))
    email_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_events.id"))
    po_line_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.po_lines.id"))
    status: Mapped[str] = mapped_column(String(30), default="running")
    current_node: Mapped[str | None] = mapped_column(String(100))
    run_type: Mapped[str] = mapped_column(String(30), default="email_ingest")
    total_threads: Mapped[int] = mapped_column(Integer, default=0)
    completed_threads: Mapped[int] = mapped_column(Integer, default=0)
    waiting_threads: Mapped[int] = mapped_column(Integer, default=0)
    failed_threads: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB_OR_JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowThreadORM(Base):
    """UI-facing workflow thread for one source email."""

    __tablename__ = "workflow_threads"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    thread_id: Mapped[str] = mapped_column(String(50), unique=True)
    batch_id: Mapped[str] = mapped_column(String(50))
    agent_run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    email_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_events.id"))
    po_line_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.po_lines.id"))
    source_message_id: Mapped[str | None] = mapped_column(String(300))
    sender: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    current_node: Mapped[str | None] = mapped_column(String(100))
    stage: Mapped[str] = mapped_column(String(30), default="INGESTING")
    cmir_status: Mapped[str | None] = mapped_column(String(30))
    latest_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON)
    pending_action_id: Mapped[UUID | None] = mapped_column(UUID_PK)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PendingHumanActionORM(Base):
    """Open or completed human-review interrupt."""

    __tablename__ = "pending_human_actions"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    batch_id: Mapped[str] = mapped_column(String(50))
    agent_run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    thread_id: Mapped[str] = mapped_column(String(50), ForeignKey(f"{CMIR_SCHEMA}.workflow_threads.thread_id"))
    email_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_events.id"))
    po_line_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.po_lines.id"))
    interrupt_type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB_OR_JSON)
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB_OR_JSON)
    status: Mapped[str] = mapped_column(String(30), default="open")
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON)
    actor: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentTraceORM(Base):
    """One row per LangGraph node execution."""

    __tablename__ = "agent_traces"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    batch_id: Mapped[str | None] = mapped_column(String(50))
    thread_id: Mapped[str | None] = mapped_column(String(50))
    node_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON)
    output_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HITLActionORM(Base):
    """Audit trail of human answers and draft edits."""

    __tablename__ = "hitl_actions"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    batch_id: Mapped[str | None] = mapped_column(String(50))
    thread_id: Mapped[str | None] = mapped_column(String(50))
    email_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_events.id"))
    interrupt_type: Mapped[str] = mapped_column(String(30))
    question: Mapped[dict[str, Any]] = mapped_column(JSONB_OR_JSON)
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB_OR_JSON)
    decision: Mapped[str | None] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(String(300))
    actor: Mapped[str] = mapped_column(String(50))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    action_type: Mapped[str | None] = mapped_column(String(30))
    field_changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON)
    po_line_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.po_lines.id"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
