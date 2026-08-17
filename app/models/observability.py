from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentRunORM(Base):
    """One per-email agent execution grouped by batch_id."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[Optional[str]] = mapped_column(Text)
    thread_id: Mapped[Optional[str]] = mapped_column(Text)
    email_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
    po_line_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
    status: Mapped[str] = mapped_column(String, default="running")
    current_node: Mapped[Optional[str]] = mapped_column(Text)
    run_type: Mapped[str] = mapped_column(Text, default="email_ingest")
    total_threads: Mapped[int] = mapped_column(Integer, default=0)
    completed_threads: Mapped[int] = mapped_column(Integer, default=0)
    waiting_threads: Mapped[int] = mapped_column(Integer, default=0)
    failed_threads: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)


class WorkflowThreadORM(Base):
    """UI-facing workflow thread for one source email."""

    __tablename__ = "workflow_threads"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    thread_id: Mapped[str] = mapped_column(Text, unique=True)
    batch_id: Mapped[str] = mapped_column(Text)
    agent_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("agent_runs.id"))
    email_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("email_events.id"))
    po_line_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("po_lines.id"))
    source_message_id: Mapped[Optional[str]] = mapped_column(Text)
    sender: Mapped[Optional[str]] = mapped_column(Text)
    subject: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="running")
    current_node: Mapped[Optional[str]] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text, default="INGESTING")
    cmir_status: Mapped[Optional[str]] = mapped_column(Text)
    latest_snapshot: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    pending_action_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)


class PendingHumanActionORM(Base):
    """Open or completed human-review interrupt."""

    __tablename__ = "pending_human_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Text)
    agent_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("agent_runs.id"))
    thread_id: Mapped[str] = mapped_column(Text, ForeignKey("workflow_threads.thread_id"))
    email_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("email_events.id"))
    po_line_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("po_lines.id"))
    interrupt_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB)
    state_snapshot: Mapped[Dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(Text, default="open")
    answer: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    actor: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AgentTraceORM(Base):
    """One row per LangGraph node execution."""

    __tablename__ = "agent_trace"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("agent_runs.id"))
    batch_id: Mapped[Optional[str]] = mapped_column(Text)
    thread_id: Mapped[Optional[str]] = mapped_column(Text)
    node_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    input_snapshot: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    output_snapshot: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)


class HITLActionORM(Base):
    """Audit trail of human answers and draft edits."""

    __tablename__ = "hitl_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("agent_runs.id"))
    batch_id: Mapped[Optional[str]] = mapped_column(Text)
    email_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
    interrupt_type: Mapped[str] = mapped_column(Text)
    question: Mapped[Dict[str, Any]] = mapped_column(JSON)
    answer: Mapped[Dict[str, Any]] = mapped_column(JSON)
    decision: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    thread_id: Mapped[Optional[str]] = mapped_column(Text)
    action_type: Mapped[Optional[str]] = mapped_column(Text)
    field_changes: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    po_line_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
