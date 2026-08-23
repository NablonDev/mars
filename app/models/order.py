"""Database model for the order header used by the fine projection engine."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7


class Order(Base):
    __tablename__ = "sales_order"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    retailer_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.retailer.retailer_id"))
    sku_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sku.sku_id"))
    ship_from_location_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.location.location_id"))
    order_qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    order_date: Mapped[date] = mapped_column(Date)
    requested_delivery_date: Mapped[date] = mapped_column(Date)
    required_ship_date: Mapped[date] = mapped_column(Date)
    order_status: Mapped[str] = mapped_column(String(30), default="OPEN")  # OPEN / DELIVERED / CANCELLED
    carrier_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{FINES_SCHEMA}.carrier.carrier_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
