"""API endpoints for `cmir.email_event` ingestion.

Thread-lifecycle routes (stage/snapshot, missing-fields, draft updates,
decisions) live on `app/api/v1/workflow_threads.py` -- `workflow_thread` is a
shared `process`-schema resource used by both `cmir` and `po_validation`, not
owned by either domain's own router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_service
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import ValidationError
from app.schemas.cmir.email_events import IngestEmailEventsRequest, IngestEmailEventsResponse
from app.services.cmir.run_service import CmirRunService

router = APIRouter(tags=["cmir"])


@router.post(
    "/cmir/email-events",
    response_model=Envelope[IngestEmailEventsResponse],
    status_code=202,
)
def start_email_ingest(
    body: IngestEmailEventsRequest,
    run_service: CmirRunService = Depends(get_service),
) -> Envelope[IngestEmailEventsResponse]:
    if body.source != "gmail":
        raise ValidationError(
            code="VALIDATION_ERROR",
            message="Only gmail source is currently configured.",
            details={"source": body.source},
        )
    result = run_service.start_email_ingest(
        max_workers=body.max_workers,
        subject_contains=body.filters.subject_contains,
        unread_only=body.filters.unread_only,
    )
    return success_envelope(IngestEmailEventsResponse.model_validate(result), message="Email ingest started.")
