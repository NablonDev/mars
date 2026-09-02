"""API endpoints for the PO Validation ingest pipeline and `purchase_order_line`
listing.

Processing-error listing lives on `app/api/v1/processing_errors.py` and
thread-lifecycle decisions on `app/api/v1/workflow_threads.py` --
`processing_error`/`workflow_thread` are shared `process`-schema resources
used by both `cmir` and `po_validation`, not owned by either domain's own
router.

`GET /purchase-order-lines` used to be two routes for the same resource: this
flat, paginated, cross-PO listing, and a separate nested
`GET /purchase-orders/{purchase_order_id}/lines` (unpaginated, single-PO).
Consolidated into the one route below, same "no 2-3 different URL shapes for
one resource" reasoning as `app.api.v1.penalties.projections`/`mitigations`
-- see `PoValidationService.list_ready_lines`'s docstring for the exact
`purchase_order_id`/`status` semantics preserved from each.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_po_service, get_purchase_order_repository
from app.core.envelope import Envelope, success_envelope
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.schemas.po_validation.purchase_order_lines import (
    IngestPurchaseOrderLinesRequest,
    IngestPurchaseOrderLinesResponse,
    PurchaseOrderLinesListResponse,
)
from app.services.po_validation.service import PoValidationService

router = APIRouter(tags=["po-validation"])


@router.post(
    "/po-validation/purchase-order-lines",
    response_model=Envelope[IngestPurchaseOrderLinesResponse],
    status_code=202,
)
def ingest_purchase_order_lines(
    body: IngestPurchaseOrderLinesRequest,
    po_service: PoValidationService = Depends(get_po_service),
) -> Envelope[IngestPurchaseOrderLinesResponse]:
    """Domain-scoped ingest action creating `common.purchase_order_line` rows:
    kept under `/po-validation/` since it's the validation pipeline's entry
    point (runs CMIR matching/material-master checks as a side effect of
    ingest), not a generic `common.purchase_order_line` CRUD create."""
    result = po_service.ingest_po_lines([line.model_dump() for line in body.lines])
    return success_envelope(
        IngestPurchaseOrderLinesResponse.model_validate(result), message="Purchase order lines ingested."
    )


@router.get("/purchase-order-lines", response_model=Envelope[PurchaseOrderLinesListResponse])
def list_purchase_order_lines(
    purchase_order_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    purchase_orders: PurchaseOrderRepository = Depends(get_purchase_order_repository),
    po_service: PoValidationService = Depends(get_po_service),
) -> Envelope[PurchaseOrderLinesListResponse]:
    """The one `purchase_order_line` listing route -- see
    `PurchaseOrderLinesListResponse`'s and `PoValidationService.
    list_ready_lines`'s docstrings for the exact `purchase_order_id`/
    `status` semantics."""
    if purchase_order_id is not None:
        purchase_orders.require_purchase_order(purchase_order_id)
    result = po_service.list_ready_lines(
        purchase_order_id=purchase_order_id, status=status, limit=limit, cursor=cursor
    )
    return success_envelope(PurchaseOrderLinesListResponse.model_validate(result))
