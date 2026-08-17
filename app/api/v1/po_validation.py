from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_po_service
from app.schemas.po_validation import (
    IngestPoLinesRequest,
    IngestPoLinesResponse,
    ManualCmirEntryRequest,
    PoLineErrorsResponse,
    PoLinesListResponse,
    PoThreadStageResponse,
    QtyMismatchDecisionRequest,
)
from app.services.po_validation_service import PoValidationService


def create_router() -> APIRouter:
    """Build the PO Validation routes (PRD §11.1)."""
    router = APIRouter(prefix="/api/v1", tags=["po-validation"])

    @router.post("/ingest/po-lines", response_model=IngestPoLinesResponse, status_code=202)
    def ingest_po_lines(
        request_body: IngestPoLinesRequest,
        po_service: PoValidationService = Depends(get_po_service),
    ):
        return po_service.ingest_po_lines([line.model_dump() for line in request_body.lines])

    @router.get("/po-lines", response_model=PoLinesListResponse)
    def list_po_lines(
        status: Optional[str] = None,
        limit: int = Query(default=50, ge=1, le=200),
        cursor: Optional[str] = None,
        po_service: PoValidationService = Depends(get_po_service),
    ):
        return po_service.list_ready_lines(status=status, limit=limit, cursor=cursor)

    @router.get("/po-lines/{po_line_id}/errors", response_model=PoLineErrorsResponse)
    def get_po_line_errors(
        po_line_id: str,
        po_service: PoValidationService = Depends(get_po_service),
    ):
        return po_service.get_errors(po_line_id)

    @router.post("/threads/{thread_id}/qty-mismatch-decision", response_model=PoThreadStageResponse)
    def submit_qty_mismatch_decision(
        thread_id: str,
        request_body: QtyMismatchDecisionRequest,
        po_service: PoValidationService = Depends(get_po_service),
    ):
        return po_service.submit_qty_mismatch_decision(
            thread_id,
            actor=request_body.actor,
            decision=request_body.decision,
            substitute_material_code=request_body.substitute_material_code,
            expected_updated_at=request_body.expected_updated_at,
        )

    @router.post("/threads/{thread_id}/manual-cmir-entry", response_model=PoThreadStageResponse)
    def submit_manual_cmir_entry(
        thread_id: str,
        request_body: ManualCmirEntryRequest,
        po_service: PoValidationService = Depends(get_po_service),
    ):
        return po_service.submit_manual_cmir_entry(
            thread_id,
            actor=request_body.actor,
            sap_material_number=request_body.sap_material_number,
            description=request_body.description,
            expected_updated_at=request_body.expected_updated_at,
        )

    return router
