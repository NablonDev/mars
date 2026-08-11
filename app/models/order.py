"""The order header. fact_order.unit_price and other additions versus the
conceptual schema doc are explained in docs/mars_fines_projection_schema.sql
and docs/FINE_ENGINE.md -- the engine cannot price PERCENT_OF_PO fines
without unit_price, so it lives here even though the original design
didn't have it."""

from datetime import date
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, generate_uuid7


class OrderORM(Base):
    __tablename__ = "fact_order"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    retailer_id: Mapped[str] = mapped_column(ForeignKey("dim_retailer.retailer_id"))
    sku_id: Mapped[str] = mapped_column(ForeignKey("dim_sku.sku_id"))
    ship_from_location_id: Mapped[str] = mapped_column(ForeignKey("dim_location.location_id"))
    order_qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    order_date: Mapped[date] = mapped_column(Date)
    requested_delivery_date: Mapped[date] = mapped_column(Date)
    required_ship_date: Mapped[date] = mapped_column(Date)
    order_status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN / DELIVERED / CANCELLED
    carrier_id: Mapped[str | None] = mapped_column(ForeignKey("dim_carrier.carrier_id"), nullable=True)
