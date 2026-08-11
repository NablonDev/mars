"""
Daily operational facts an ETL job (or the demo scripts) writes: SAP
cut-order confirmations, production status, shipment/appointment state,
and demand exceptions. OrderRepository.build_snapshot reads the latest
row "as of" a projection date from each of these to assemble the
engine's OrderSnapshot.

fact_production_schedule is keyed by (sku_id, location_id), not order_id
-- a production run serves whichever orders draw on that SKU at that
plant, it isn't owned by a single order. See docs/FINE_ENGINE.md and
docs/mars_fines_projection_schema.sql for the historization rationale
behind fact_shipment specifically.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, generate_uuid7


class OrderConfirmation(Base):
    __tablename__ = "fact_order_confirmation"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    confirmation_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("fact_order.order_id"))
    confirmed_qty: Mapped[int] = mapped_column(Integer)
    confirmation_date: Mapped[datetime] = mapped_column(DateTime)
    cut_reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ProductionSchedule(Base):
    __tablename__ = "fact_production_schedule"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    production_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey("dim_sku.sku_id"))
    location_id: Mapped[str] = mapped_column(ForeignKey("dim_location.location_id"))
    status: Mapped[str] = mapped_column(String(20))  # ON_TRACK / AT_RISK / BEHIND
    status_date: Mapped[datetime] = mapped_column(DateTime)


class Shipment(Base):
    """Historized, append-only -- one row per shipment-fact update, not a
    single current-state row. See docs/FINE_ENGINE.md changelog."""

    __tablename__ = "fact_shipment"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    shipment_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("fact_order.order_id"))
    carrier_id: Mapped[str | None] = mapped_column(ForeignKey("dim_carrier.carrier_id"), nullable=True)
    expected_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    appointment_status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    expected_transit_days: Mapped[int] = mapped_column(Integer, default=2)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)


class DemandException(Base):
    __tablename__ = "fact_demand_exception"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    exception_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("fact_order.order_id"))
    flagged_date: Mapped[date] = mapped_column(Date)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
