"""API endpoint for `process.processing_error` (was `GET /po-lines/{id}/errors`).

`processing_error` is a `process`-schema table shared across domains
(approved plan §6) -- flat + filter, not nested two levels deep under
`/purchase-order-lines/{id}/errors`. `PoValidationService.get_errors` is
this phase's only caller (see that method's docstring for the discoverability
gap on a line that failed before ever reaching a human interrupt); a
`penalties`-side error listing would filter by a different query param on
this same route, once one exists.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_po_service
from app.core.envelope import Envelope, success_envelope
from app.schemas.po_validation.processing_errors import ProcessingErrorsListResponse
from app.services.po_validation.service import PoValidationService

router = APIRouter(tags=["processing-errors"])


@router.get("/processing-errors", response_model=Envelope[ProcessingErrorsListResponse])
def list_processing_errors(
    purchase_order_line_id: UUID = Query(...),
    po_service: PoValidationService = Depends(get_po_service),
) -> Envelope[ProcessingErrorsListResponse]:
    result = po_service.get_errors(purchase_order_line_id)
    return success_envelope(ProcessingErrorsListResponse.model_validate(result))
