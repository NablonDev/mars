"""Order confirmation header and line -- historized, append-only facts."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, TimestampMixin, generate_uuid7


class OrderConfirmation(Base, TimestampMixin):
    __tablename__ = "order_confirmation"

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    confirmation_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    purchase_order_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order.id"))
    confirmation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)


class OrderConfirmationLine(Base, TimestampMixin):
    __tablename__ = "order_confirmation_line"
    __table_args__ = (
        UniqueConstraint(
            "order_confirmation_id", "purchase_order_line_id", name="uq_order_confirmation_line_line"
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_confirmation_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("order_confirmation.id"))
    purchase_order_line_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order_line.id"))
    confirmed_quantity: Mapped[float] = mapped_column(Numeric(18, 3))
    confirmed_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cut_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
