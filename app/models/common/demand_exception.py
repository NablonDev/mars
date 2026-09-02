"""Demand exceptions flagged against a PO line -- a fulfillment fact like
`order_confirmation`/`shipment`. Not in docs/redesigned-schema.md's own
table listing (only mentioned in its "gap-fill" answers); this module
implements that answer: `public` schema (unqualified), FK'd to
`purchase_order_line_id`."""

from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, TimestampMixin, generate_uuid7


class DemandException(Base, TimestampMixin):
    __tablename__ = "demand_exception"

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    exception_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    purchase_order_line_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order_line.id"))
    flagged_date: Mapped[date] = mapped_column(Date)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
