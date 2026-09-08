"""Cause and cost assumptions feeding mitigation ranking, plus the ranked options themselves."""

from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import PENALTIES_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class MitigationInput(Base, TimestampMixin):
    """Shortage cause and mitigation cost assumptions for a purchase order.

    Stores user-provided or inferred data for penalty mitigation ranking.
    Mutable; one row per PO with unique constraint.
    """

    __tablename__ = "mitigation_input"
    __table_args__ = ({"schema": PENALTIES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    purchase_order_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey("purchase_order.id"), unique=True, index=True
    )
    shortage_cause: Mapped[str] = mapped_column(String(50), default="UNKNOWN")
    shortage_cause_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    capacity_boost_cost_per_unit: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    capacity_boost_max_units_per_day: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    capacity_boost_data_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    express_carrier_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    express_carrier_transit_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    express_carrier_data_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    split_shipment_handling_cost: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)


class MitigationOption(Base, TimestampMixin):
    """Ranked mitigation action for a purchase order and projection date.

    Persists mitigation engine output with savings, costs, and confidence ratings.
    Keyed by (purchase_order_id, projection_date, action) with idempotent writes.
    """

    __tablename__ = "mitigation_option"
    __table_args__ = (
        UniqueConstraint(
            "purchase_order_id",
            "projection_date",
            "action",
            name="uq_mitigation_option_po_date_action",
        ),
        {"schema": PENALTIES_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    purchase_order_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order.id"))
    projection_date: Mapped[date] = mapped_column(Date)
    action: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # ACCEPT / SPEED_UP_PRODUCTION / SPLIT_SHIPMENT / FASTER_CARRIER
    projected_penalty_after: Mapped[float] = mapped_column(Numeric(12, 2))
    action_cost: Mapped[float] = mapped_column(Numeric(12, 2))
    net_saving: Mapped[float] = mapped_column(Numeric(12, 2))
    risk_level: Mapped[str] = mapped_column(String(30))  # LOW / MEDIUM / HIGH
    confidence: Mapped[str] = mapped_column(String(30))  # CONFIRMED / ESTIMATED
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
