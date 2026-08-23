"""Cause/cost assumptions feeding the mitigation-ranking stage."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7


class MitigationInput(Base):
    """One row per order: the current best-guess cause/cost assumptions
    used to rank mitigation options. Mutable -- represents a current
    assumption, not a historized event, deliberately unlike
    order_confirmation/production_schedule/shipment (which
    ARE append-only)."""

    __tablename__ = "mitigation_input"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(
        ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"), unique=True, index=True
    )
    shortage_cause: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    shortage_cause_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    capacity_boost_cost_per_unit: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    capacity_boost_max_units_per_day: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    capacity_boost_data_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    express_carrier_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    express_carrier_transit_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    express_carrier_data_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    split_shipment_handling_cost: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
