"""Database model for vendor-initiated PO delivery-date change requests.

Was `po_delivery_change_request`, renamed to `purchase_order_delivery_change_request`
in an earlier pass (FK changed at the same time to point at the surrogate
`purchase_order.id` rather than the business-key `sales_order.order_id`),
then renamed back to `po_delivery_change_request` in this pass (architecture
review) -- the FK still points at the surrogate id; only the long-form
table/class name reverted.

Lives in the shared `common` (unqualified/public) schema, not `penalties` --
this is a procurement/EDI concept (vendor delivery-date renegotiation, SAP
ORDRSP/EDI-865 equivalent), not a penalty-calculation concept, and the
penalty projection/mitigation engines never read this table. Its sibling
state (`purchase_order.current_delivery_date`/`.negotiation_status`,
`retailer.extension_min_lead_days`/`.extension_response_sla_hours`/
`.extension_penalty_threshold`) already lived unqualified in `common` --
this table was the one piece still misplaced in `penalties`.

One row represents one request and its lifecycle. A PO may have multiple
requests over time; the latest request determines the current request
state (real-world equivalent: EDI 865 / SAP ORDRSP -- EDI 860/ORDCHG is
buyer-initiated only, not this).
"""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    JSONB_OR_JSON,
    UUID_PK,
    Base,
    TimestampMixin,
    generate_uuid7,
)


def generate_request_id() -> str:
    """External-system correlation key -- not read by any lookup on this
    side (`get_by_id` is the sole lookup; there is no `get_by_request_id`),
    kept for a future real integration to reconcile against its own
    identifier."""
    return f"ext_{uuid4().hex[:12]}"


class PoDeliveryChangeRequest(Base, TimestampMixin):
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
        Index("ix_po_delivery_change_request_po_status", "purchase_order_id", "status"),
        Index("ix_po_delivery_change_request_status_expires", "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    request_id: Mapped[str] = mapped_column(String(50), unique=True, index=True, default=generate_request_id)
    purchase_order_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order.id"))
    reason_code: Mapped[str] = mapped_column(String(30))  # SHORTAGE / DELAY / OTHER
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    baseline_delivery_date: Mapped[date] = mapped_column(Date)  # audit snapshot, not read live
    proposed_delivery_date: Mapped[date] = mapped_column(Date)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # requested_at + response_sla_hours
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    # PENDING / ACCEPTED / COUNTERED / REJECTED / EXPIRED
    retailer_response_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    countered_delivery_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )  # set only when COUNTERED
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(
        JSONB_OR_JSON, nullable=True
    )  # raw retailer-response payload, for future real webhook debugging
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
