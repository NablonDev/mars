"""System/lookup failures -- distinct from `human_action` (human
decisions). Generalizes the old CMIR-only `PoLineErrorORM`; shared by both
domains via FKs to `process.job_item`/`process.agent_run`.

`purchase_order_line_id` (added alongside this docstring update, closing a
previously-flagged gap -- see `PoValidationService.get_errors`'s history)
lets a PO-validation line's error be found directly, without first needing
a `workflow_thread` to exist for it: `handle_error` (`app/agents/
po_validation/nodes.py`) is reached from every pre-interrupt node
(`persist_po_line`/`validate_against_cmir`/`check_material_master`/
`create_cmir_record`), and a `workflow_thread` is only ever created lazily,
on a line's first human interrupt -- a line that fails before ever
reaching one previously had no discoverable error trail at all."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    JSONB_OR_JSON,
    PROCESS_SCHEMA,
    UUID_PK,
    Base,
    TimestampMixin,
    generate_uuid7,
)


class ProcessingError(Base, TimestampMixin):
    __tablename__ = "processing_error"
    __table_args__ = ({"schema": PROCESS_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    job_item_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_item.id"), nullable=True
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.agent_run.id"), nullable=True
    )
    purchase_order_line_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("purchase_order_line.id"), nullable=True
    )
    error_type: Mapped[str] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_error_detail: Mapped[dict | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
