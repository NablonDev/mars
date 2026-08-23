"""Database model for persisted fine-mitigation-summary generation jobs.

Full mirror of app/models/fine_projection/summary.py -- same key shape,
same PENDING/READY/FAILED lifecycle, same fingerprint/reuse columns. See
that module for the field-level rationale; not repeated here.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7
from app.models.enums import SummaryStatus


class MitigationSummary(Base):
    __tablename__ = "mitigation_summary"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "as_of_date",
            "prompt_version",
            name="uq_fine_mitigation_summary_order_date_prompt",
        ),
        ForeignKeyConstraint(
            ["agent_id", "prompt_version"],
            [
                f"{FINES_SCHEMA}.prompt_version.agent_id",
                f"{FINES_SCHEMA}.prompt_version.prompt_version",
            ],
            name="fk_fine_mitigation_summary_agent_prompt_version",
        ),
        {"schema": FINES_SCHEMA},
    )
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    agent_id: Mapped[UUID] = mapped_column(UUID_PK)
    as_of_date: Mapped[date] = mapped_column(Date)
    prompt_version: Mapped[str] = mapped_column(String(20))
    context_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex digest, diagnostic only
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)  # sha256 hex digest of the mitigation-options content that drove the narrative
    source_as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # the date the narrative was actually generated for, when a summary is reused across days
    status: Mapped[str] = mapped_column(String(30), default=SummaryStatus.PENDING)  # PENDING / READY / FAILED
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # free-text; null until READY
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)  # set only when FAILED
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
