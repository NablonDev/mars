"""API schemas for `POST /api/v1/po-validation/purchase-order-lines` (was
`POST /ingest/po-lines`) and the flat `GET /api/v1/purchase-order-lines`
cross-PO listing (was `GET /po-lines`).

Class names drop the stale `po_line`/`Po*` abbreviation in favor of the full
`purchase_order_line` wording (approved plan's locked-in naming decision),
per §6's "Rename DTO classes dropping stale prefixes where the rest of the
rename already applies elsewhere." Wire-level payload field names
(`po_number`, `po_line_number`, ...) are left unchanged -- they are read
directly by `PoValidationService._ingest_one_line` (`payload["po_number"]`,
...), a service-layer contract out of scope for this API-surface phase.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.cmir.threads import IsoDatetime


class IngestPurchaseOrderLineItem(BaseModel):
    po_number: str
    po_line_number: str
    customer_id: str
    customer_material_code: str
    plant: str
    order_quantity: float
    uom: str | None = None
    requested_delivery_date: str | None = None


class IngestPurchaseOrderLinesRequest(BaseModel):
    lines: list[IngestPurchaseOrderLineItem] = Field(min_length=1)


class PurchaseOrderLineIngestSummary(BaseModel):
    po_line_id: str
    batch_id: str
    po_number: str
    po_line_number: str
    status: str
    thread_id: str | None = None
    # `PoValidationService._ingest_one_line`'s touchless path always sends
    # `None`; once a `workflow_thread` exists (first interrupt or later),
    # this is that thread's real `updated_at` -- round-trippable straight
    # into `POST /workflow-threads/{thread_id}/decisions`' `expected_updated_at`
    # without an intermediate GET, so it uses the same `IsoDatetime` as
    # `WorkflowThreadResponse.updated_at` (see that type's docstring).
    updated_at: IsoDatetime | None = None


class IngestPurchaseOrderLinesResponse(BaseModel):
    batch_id: str
    total_lines: int
    lines: list[PurchaseOrderLineIngestSummary]


class PurchaseOrderLinesListResponse(BaseModel):
    """Cross-PO, filtered listing -- `PoValidationService.list_ready_lines`
    has no repository support for this today (no "list every
    purchase_order_line by line_status across every PO" query exists on
    `PurchaseOrderRepository`; only `list_lines(purchase_order_id)`, scoped
    to one PO) -- see that service method's docstring, point 4. The route
    surfaces `ValidationError(code="VIEW_NOT_SUPPORTED")` rather than
    faking an unindexed full scan; use
    `GET /purchase-orders/{purchase_order_id}/lines` for a single PO's lines
    in the meantime.
    """

    items: list[dict[str, Any]]
    next_cursor: str | None = None
