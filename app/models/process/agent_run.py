"""One independent agent execution and its per-node execution trace."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSONB_OR_JSON, PROCESS_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class AgentRun(Base, TimestampMixin):
    """One row per independent agent execution (one email, one PO line,
    one order projection, ...)."""

    __tablename__ = "agent_run"
    __table_args__ = ({"schema": PROCESS_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    job_item_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_item.id"), nullable=True
    )
    workflow_thread_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.workflow_thread.id"), nullable=True
    )
    agent_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.agent.id"))
    status: Mapped[str] = mapped_column(String(50), default="running")
    run_type: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)


class AgentTrace(Base, TimestampMixin):
    """One row per LangGraph node execution."""

    __tablename__ = "agent_trace"
    __table_args__ = ({"schema": PROCESS_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    agent_run_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.agent_run.id"))
    node_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    input_snapshot: Mapped[dict | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    output_snapshot: Mapped[dict | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
