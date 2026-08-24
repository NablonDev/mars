"""Database model for persisted fine-projection-summary jobs."""

from datetime import date
from uuid import UUID

from sqlalchemy import Date, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7
from app.models.enums import SummaryStatus


class ProjectionSummary(Base, TimestampMixin):
    __tablename__ = "projection_summary"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "as_of_date",
            "prompt_version",
            name="uq_fine_projection_summary_order_date_prompt",
        ),
        ForeignKeyConstraint(
            ["agent_id", "prompt_version"],
            [
                f"{FINES_SCHEMA}.prompt_version.agent_id",
                f"{FINES_SCHEMA}.prompt_version.prompt_version",
            ],
            name="fk_fine_projection_summary_agent_prompt_version",
        ),
        {"schema": FINES_SCHEMA},
    )
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    agent_id: Mapped[UUID] = mapped_column(UUID_PK)
    as_of_date: Mapped[date] = mapped_column(Date)
    prompt_version: Mapped[str] = mapped_column(String(50))
    context_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex digest, diagnostic only
    content_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # sha256 hex digest of the generated narrative; nullable so existing rows migrate cleanly
    source_as_of_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )  # the date the narrative was actually generated for, when a summary is reused across days
    status: Mapped[str] = mapped_column(String(30), default=SummaryStatus.PENDING)  # PENDING / READY / FAILED
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # free-text; null until READY
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)  # set only when FAILED
