"""The reviewer-facing HITL workflow thread and its polymorphic subject.

D1 (FK-cycle resolution, see the approved Phase 1 plan): `WorkflowThreadSubject`
is declared here (in `process`), but its table is physically created by the
`cmir` schema's migration, placed after `cmir.email_event` and before
`cmir.job_item_context` -- Postgres needs `cmir.email_event` to already
exist when `workflow_thread_subject`'s FK to it is created, and migrations
run in schema order (`public` -> `process` -> `cmir` -> `penalties`).
"""

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
    """UI-facing workflow thread. Only created once a job item's first
    human interrupt fires -- not every job_item has one."""

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
    """1:1 subtype/extension of `WorkflowThread`, replacing a polymorphic
    `subject_type`/`subject_id` pair with two nullable typed FKs.

    Exactly one of `email_event_id` / `purchase_order_line_id` must be set,
    enforced by `CHECK (num_nonnulls(email_event_id,
    purchase_order_line_id) = 1)` -- a PostgreSQL-only builtin, so the CHECK
    is declared as raw migration DDL only (see the `cmir` schema revision),
    never here: SQLAlchemy would happily compile it into a CheckConstraint
    on SQLite too, but `num_nonnulls` doesn't exist there, breaking
    `Base.metadata.create_all()` for the whole test suite.

    Both FKs are declared on the ORM model (unlike the CHECK above, which
    can't be): the `cmir` schema migration already creates
    `ForeignKeyConstraint`s for both columns (it must -- Postgres validates
    every row against them), so the model has to match, same two-nullable-FK
    shape as `CmirJobItemContext` (`app/models/cmir/job_context.py`).
    """

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
