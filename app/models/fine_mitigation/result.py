"""Database model for persisted, ranked mitigation options."""

from datetime import date
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class MitigationResult(Base, TimestampMixin):
    """One row per (order_id, projection_date, action): the persisted,
    ranked output of app/services/fine_mitigation/engine.py for a given
    order and projection day. Append-only like projected_fine --
    re-running mitigation for the same (order_id, projection_date) upserts
    each action's row in place rather than inserting a duplicate."""

    __tablename__ = "mitigation_option"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "projection_date",
            "action",
            name="uq_mitigation_result_order_date_action",
        ),
        {"schema": FINES_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    projection_date: Mapped[date] = mapped_column(Date)
    action: Mapped[str] = mapped_column(
        String(30), nullable=True
    )  # ACCEPT / SPEED_UP_PRODUCTION / SPLIT_SHIPMENT / FASTER_CARRIER
    projected_fine_after: Mapped[float] = mapped_column(Numeric(12, 2))
    action_cost: Mapped[float] = mapped_column(Numeric(12, 2))
    net_saving: Mapped[float] = mapped_column(Numeric(12, 2))
    risk_level: Mapped[str] = mapped_column(String(30))  # LOW / MEDIUM / HIGH
    confidence: Mapped[str] = mapped_column(String(30))  # CONFIRMED / ESTIMATED
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
