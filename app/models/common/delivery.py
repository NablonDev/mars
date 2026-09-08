"""Delivery header, delivery line, and the shipment fact: historized, append-only."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, TimestampMixin, generate_uuid7


class Delivery(Base, TimestampMixin):
    """Delivery header (partial or full fulfillment of a PO).

    Tracks dates and status for a shipment covering one or more PO lines.
    Append-only; one row per delivery event.
    """

    __tablename__ = "delivery"

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    delivery_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    purchase_order_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order.id"))
    ship_from_plant_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("plant.id"), nullable=True)
    ship_from_warehouse_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("warehouse.id"), nullable=True
    )
    ship_to_location_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("retailer_location.id"), nullable=True
    )
    delivery_status: Mapped[str] = mapped_column(String(50), default="OPEN")
    planned_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    goods_issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class DeliveryLine(Base, TimestampMixin):
    """Delivery line matching a PO line with actual delivered quantity.

    Keyed by (delivery_id, purchase_order_line_id).
    """

    __tablename__ = "delivery_line"
    __table_args__ = (
        UniqueConstraint("delivery_id", "purchase_order_line_id", name="uq_delivery_line_delivery_po_line"),
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    delivery_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("delivery.id"))
    purchase_order_line_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order_line.id"))
    delivered_quantity: Mapped[float] = mapped_column(Numeric(18, 3))
    uom: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Shipment(Base, TimestampMixin):
    """Historized shipment facts (append-only) capturing status snapshots.

    One row per status update; tracks dates, carrier, and transit information.
    """

    __tablename__ = "shipment"

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    shipment_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    delivery_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("delivery.id"))
    carrier_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("carrier.id"), nullable=True)
    expected_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_transit_days: Mapped[int | None] = mapped_column(Integer, default=2, nullable=True)
    appointment_status: Mapped[str | None] = mapped_column(String(50), default="SCHEDULED", nullable=True)
    shipment_status: Mapped[str] = mapped_column(String(50), default="SCHEDULED")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
