"""Database models for historized daily operational facts."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7


class OrderConfirmation(Base):
    __tablename__ = "order_confirmation"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    confirmation_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    confirmed_qty: Mapped[int] = mapped_column(Integer)
    confirmation_date: Mapped[datetime] = mapped_column(DateTime)
    cut_reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProductionSchedule(Base):
    __tablename__ = "production_schedule"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    production_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sku.sku_id"))
    location_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.location.location_id"))
    status: Mapped[str] = mapped_column(String(20))  # ON_TRACK / AT_RISK / BEHIND
    status_date: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Shipment(Base):
    """Historized, append-only shipment facts; one row per update."""

    __tablename__ = "shipment"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    shipment_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    carrier_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{FINES_SCHEMA}.carrier.carrier_id"), nullable=True
    )
    expected_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    appointment_status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    expected_transit_days: Mapped[int] = mapped_column(Integer, default=2)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DemandException(Base):
    __tablename__ = "demand_exception"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    exception_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    flagged_date: Mapped[date] = mapped_column(Date)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
