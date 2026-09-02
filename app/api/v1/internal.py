"""Internal Service Bus consumer callback for CMIR email processing.

`POST /internal/process-email` is not a PRD-facing route -- it's the queue
consumer's own internal call, kept separate from the public `cmir/email-events`
resource for exactly that reason.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_service
from app.core.envelope import Envelope, success_envelope
from app.schemas.cmir.email_events import ProcessQueuedEmailRequest
from app.services.cmir.run_service import CmirRunService

router = APIRouter(tags=["internal"])


@router.post("/internal/process-email", response_model=Envelope[dict[str, Any]])
def process_queued_email(
    body: ProcessQueuedEmailRequest,
    run_service: CmirRunService = Depends(get_service),
) -> Envelope[dict[str, Any]]:
    """Internal Service Bus consumer call, not a PRD-facing route -- the
    result shape is polymorphic (a fresh graph run's thread-stage dict, the
    touchless-path literal, or an already-processed/failed summary; see
    `CmirRunService.process_queued_email`), so this stays untyped rather than
    forcing a single response model onto genuinely different shapes."""
    result = run_service.process_queued_email(
        batch_id=body.batch_id,
        email=body.email,
        queue_message_id=body.queue_message_id,
        email_id=body.email_id,
    )
    return success_envelope(result)
