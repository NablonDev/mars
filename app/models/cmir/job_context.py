"""CMIR-specific extension tables for `process.job_run`/`process.job_item`.

Kept domain-owned rather than folded into `process`: their columns are
genuinely different per domain, and a single generic table would need a
JSONB catch-all, strictly worse than two thin typed extension tables (see
the approved Phase 1 plan's gap-fill answers).

**Deviation from the approved plan, forced by the plan's own SQLite
translation rule:** the plan's exact file list says these tables should
keep the bare names `job_run_context`/`job_item_context` in both `cmir`
and `penalties` (distinguished only by Postgres schema), with just the
Python class names prefixed. But section 1.1 of the same plan requires
`apply_sqlite_schema_translation` to map *all four* schemas to `None` on
SQLite -- which collapses both same-named tables into one unqualified
namespace and makes `Base.metadata.create_all()` fail with "table
job_run_context already exists" (SQLite has no schema concept, so nothing
short of ATTACHing separate SQLite databases -- out of scope; the plan
caps `app/db/session.py` changes at two lines -- could keep them apart).
Table names are prefixed to match the already-adopted Python class prefix
(`cmir_job_run_context`/`cmir_job_item_context` here,
`penalty_job_run_context`/`penalty_job_item_context` in
`app/models/penalties/job_context.py`) instead.
"""

from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    CMIR_SCHEMA,
    COMMON_SCHEMA,
    JSONB_OR_JSON,
    PROCESS_SCHEMA,
    UUID_PK,
    Base,
    TimestampMixin,
)


class CmirJobRunContext(Base, TimestampMixin):
    """Extends `process.job_run` with CMIR batch-ingest metadata."""

    __tablename__ = "cmir_job_run_context"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    job_run_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_run.id"), primary_key=True
    )
    source_type: Mapped[str] = mapped_column(String(50))
    external_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    service_bus_topic: Mapped[str | None] = mapped_column(String(200), nullable=True)
    service_bus_subscription: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)


class CmirJobItemContext(Base, TimestampMixin):
    """Extends `process.job_item` with which email or PO line this work
    item is about.

    Exactly one of `email_event_id` / `purchase_order_line_id` should be
    set -- enforced by `CHECK (num_nonnulls(email_event_id,
    purchase_order_line_id) = 1)` on PostgreSQL, declared as raw migration
    DDL only (see the `cmir` schema revision), never here: `num_nonnulls`
    doesn't exist on SQLite and would break `Base.metadata.create_all()`
    for the whole test suite.
    """

    __tablename__ = "cmir_job_item_context"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    job_item_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.job_item.id"), primary_key=True
    )
    email_event_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_event.id"), nullable=True
    )
    purchase_order_line_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{COMMON_SCHEMA}.purchase_order_line.id"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB_OR_JSON, default=dict)
