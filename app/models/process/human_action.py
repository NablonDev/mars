"""One row per human decision, covering both the open interrupt and its answer's audit trail."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSONB_OR_JSON, PROCESS_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class HumanAction(Base, TimestampMixin):
    """Human decision or answer to an agent interrupt.

    Tracks request payload, response, and actor identity for HITL workflows.
    Lifecycle: open (pending) → completed (responded).
    """

    __tablename__ = "human_action"
    __table_args__ = ({"schema": PROCESS_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    job_item_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_item.id"), nullable=True
    )
    workflow_thread_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.workflow_thread.id"), nullable=True
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.agent_run.id"), nullable=True
    )
    action_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    interrupt_type: Mapped[str] = mapped_column(String(100))
    request_payload: Mapped[dict] = mapped_column(JSONB_OR_JSON)
    state_snapshot: Mapped[dict | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="open")
    response_payload: Mapped[dict | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
