"""API schemas for PO-line ingest and the cross-PO `GET /api/v1/purchase-order-lines` listing.

Wire field names keep the short `po_number`/`po_line_number` form the service layer reads.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.cmir.threads import IsoDatetime
from app.schemas.common.purchase_orders import PurchaseOrderLineResponse


class IngestPurchaseOrderLineItem(BaseModel):
    """One line of an ingest request, as received from the source order system."""

    po_number: str
    po_line_number: str
    customer_id: str
    customer_material_code: str
    plant: str
    order_quantity: float
    uom: str | None = None
    requested_delivery_date: str | None = None


class IngestPurchaseOrderLinesRequest(BaseModel):
    """Request body for `POST /api/v1/po-validation/purchase-order-lines`."""

    lines: list[IngestPurchaseOrderLineItem] = Field(min_length=1)


class PurchaseOrderLineIngestSummary(BaseModel):
    """Per-line outcome of an ingest request: the line, its status, and its thread if any."""

    po_line_id: str
    batch_id: str
    po_number: str
    po_line_number: str
    status: str
    thread_id: str | None = None
    # `None` on the touchless path; otherwise the thread's real `updated_at`, round-trippable
    # straight into a decision request's `expected_updated_at` without an intermediate GET.
    updated_at: IsoDatetime | None = None


class IngestPurchaseOrderLinesResponse(BaseModel):
    """Response shape for `POST /api/v1/po-validation/purchase-order-lines`."""

    batch_id: str
    total_lines: int
    lines: list[PurchaseOrderLineIngestSummary]


class PurchaseOrderLinesListResponse(BaseModel):
    """Paginated `purchase_order_line` listing, optionally filtered by PO and line status."""

    items: list[PurchaseOrderLineResponse]
    next_cursor: str | None = None
