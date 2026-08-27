"""Database model for the order header used by the fine projection engine."""

from datetime import date
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class Order(Base, TimestampMixin):
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
    current_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_required_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    negotiation_status: Mapped[str] = mapped_column(String(30), default="NONE", index=True)  # NONE / PENDING / ACCEPTED / COUNTERED / REJECTED / EXPIRED
    order_status: Mapped[str] = mapped_column(String(30), default="OPEN")  # OPEN / DELIVERED / CANCELLED
    carrier_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{FINES_SCHEMA}.carrier.carrier_id"), nullable=True
    )
