"""Cause/cost assumptions feeding the mitigation-ranking stage, and the
persisted, ranked mitigation options themselves."""

from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import PENALTIES_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class MitigationInput(Base, TimestampMixin):
    """One row per PO: the current best-guess cause/cost assumptions used
    to rank mitigation options. Mutable -- represents a current
    assumption, not a historized event, deliberately unlike
    order_confirmation/production_schedule/shipment (which ARE
    append-only)."""

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
    """One row per (purchase_order_id, projection_date, action): the
    persisted, ranked output of the mitigation engine for a given PO and
    projection day. Renamed from `MitigationResult` (the ORM class used to
    be kept apart from the pure-engine `MitigationOption` dataclass under
    that name -- see the approved Phase 1 plan's naming decisions, which
    now unify on this name for the ORM class instead)."""

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
