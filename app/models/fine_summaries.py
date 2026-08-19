"""Database model for persisted fine-summary jobs."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7
from app.models.enums import SummaryStatus


class FineSummary(Base):
    __tablename__ = "fact_fine_summary"
    __table_args__ = (
        UniqueConstraint(
            "order_id", "as_of_date", "prompt_version", name="uq_fine_summary_order_date_prompt"
        ),
        ForeignKeyConstraint(
            ["agent_id", "prompt_version"],
            [
                f"{FINES_SCHEMA}.dim_prompt_version.agent_id",
                f"{FINES_SCHEMA}.dim_prompt_version.prompt_version",
            ],
            name="fk_fine_summary_agent_prompt_version",
        ),
        {"schema": FINES_SCHEMA},
    )
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.fact_order.order_id"))
    as_of_date: Mapped[date] = mapped_column(Date)
    agent_id: Mapped[UUID] = mapped_column(UUID_PK)
    prompt_version: Mapped[str] = mapped_column(String(20))
    context_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex digest, diagnostic only
    content_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # sha256 hex digest of the generated narrative; nullable so existing rows migrate cleanly
    source_as_of_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )  # the date the narrative was actually generated for, when a summary is reused across days
    status: Mapped[str] = mapped_column(String(20), default=SummaryStatus.PENDING)  # PENDING / READY / FAILED
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # free-text; null until READY
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)  # set only when FAILED
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
