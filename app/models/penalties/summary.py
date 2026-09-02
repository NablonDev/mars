"""LLM-generated penalty-summary audit trail. Merges what were two
near-identical tables (`projection_summary`, `mitigation_summary`) into
one, with a `summary_type` discriminator -- their FK used to be a
composite `(agent_id, prompt_version) -> prompt_version(agent_id,
prompt_version)`; merging agent+prompt_version into one `process.agent`
row collapses this into a single-column `agent_id -> process.agent.id`."""

from datetime import date
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    PENALTIES_SCHEMA,
    PROCESS_SCHEMA,
    UUID_PK,
    Base,
    TimestampMixin,
    generate_uuid7,
)
from app.models.enums import SummaryStatus, SummaryType


class PenaltySummary(Base, TimestampMixin):
    __tablename__ = "penalty_summary"
    __table_args__ = (
        UniqueConstraint(
            "purchase_order_id",
            "summary_type",
            "as_of_date",
            name="uq_penalty_summary_po_type_date",
        ),
        CheckConstraint(
            "summary_type IN ('PROJECTION', 'MITIGATION')",
            name="ck_penalty_summary_summary_type",
        ),
        {"schema": PENALTIES_SCHEMA},
    )
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    purchase_order_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order.id"))
    summary_type: Mapped[str] = mapped_column(String(30), default=SummaryType.PROJECTION)
    as_of_date: Mapped[date] = mapped_column(Date)
    agent_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{PROCESS_SCHEMA}.agent.id"))
    context_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex digest, diagnostic only
    content_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # sha256 hex digest of the generated summary; nullable so existing rows migrate cleanly
    source_as_of_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )  # the date the summary was actually generated for, when reused across days
    status: Mapped[str] = mapped_column(String(30), default=SummaryStatus.PENDING)  # PENDING / READY / FAILED
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # free-text; null until READY
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)  # set only when FAILED
