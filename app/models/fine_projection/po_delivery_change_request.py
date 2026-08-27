"""Database model for vendor-initiated PO delivery-date change requests.

One row represents one request and its lifecycle. An order may have multiple
requests over time; the latest request determines the current request state.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, JSONB_OR_JSON, UUID_PK, Base, TimestampMixin, generate_uuid7


class PoDeliveryChangeRequest(Base, TimestampMixin):
    """Audit log for vendor-initiated PO delivery-date change requests
    (real-world equivalent: EDI 865 / SAP ORDRSP -- EDI 860/ORDCHG is
    buyer-initiated only, not this)."""

    __tablename__ = "po_delivery_change_request"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'COUNTERED', 'REJECTED', 'EXPIRED')",
            name="ck_po_delivery_change_request_status",
        ),
        CheckConstraint(
            "reason_code IN ('SHORTAGE', 'DELAY', 'OTHER')",
            name="ck_po_delivery_change_request_reason_code",
        ),
        Index("ix_po_delivery_change_request_order_status", "order_id", "status"),
        Index("ix_po_delivery_change_request_status_expires", "status", "expires_at"),
        {"schema": FINES_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    request_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    reason_code: Mapped[str] = mapped_column(String(30))  # SHORTAGE / DELAY / OTHER
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    baseline_delivery_date: Mapped[date] = mapped_column(Date)  # audit snapshot, not read live
    proposed_delivery_date: Mapped[date] = mapped_column(Date)
    expires_at: Mapped[datetime] = mapped_column(DateTime)  # requested_at + response_sla_hours
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    # PENDING / ACCEPTED / COUNTERED / REJECTED / EXPIRED
    retailer_response_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    countered_delivery_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )  # set only when COUNTERED
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(
        JSONB_OR_JSON, nullable=True
    )  # raw retailer-response payload, for future real webhook debugging
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
