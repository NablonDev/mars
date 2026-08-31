"""API endpoints for `cmir.email_event` ingestion.

Was `app/api/v1/cmir.py`'s `create_router()` factory (stale imports --
`app.core.exceptions.ServiceError`/dataclass-shaped schemas that no longer
exist post-restructure). Rewritten per the approved plan §6: module-level
`router = APIRouter(...)` (Phase 7a's convention), `Envelope[T]` on every
route, the collapsed `AppError` hierarchy.

| Old | New |
|---|---|
| `POST /ingest/emails` | `POST /api/v1/cmir/email-events` |
| `GET /runs?view=threads\\|agents\\|batches` | `GET /api/v1/job-runs?job_type=CMIR_EMAIL_INGEST` (`app/api/v1/job_runs.py`) + `GET /api/v1/workflow-threads?domain=cmir` (`app/api/v1/workflow_threads.py`) |
| `GET /threads/{id}/stage`, `GET /threads/{id}/snapshot` | `GET /api/v1/workflow-threads/{thread_id}?include=snapshot` (`app/api/v1/workflow_threads.py`) |
| `POST /threads/{id}/missing-fields` | `POST /api/v1/workflow-threads/{thread_id}/missing-fields` (`app/api/v1/workflow_threads.py`) |
| `POST /threads/{id}/update` | `PATCH /api/v1/workflow-threads/{thread_id}/draft` (`app/api/v1/workflow_threads.py`) |
| `POST /threads/{id}/decision` | `POST /api/v1/workflow-threads/{thread_id}/decisions` (`app/api/v1/workflow_threads.py`) |

`POST /internal/process-email` is not a PRD-facing route (the Service Bus
consumer's own internal call) and is not part of the plan's naming table --
kept at its existing path, just repaired against the current schemas/
exceptions/envelope.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_service
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import ValidationError
from app.schemas.cmir.email_events import (
    IngestEmailEventsRequest,
    IngestEmailEventsResponse,
    ProcessQueuedEmailRequest,
)
from app.services.cmir.run_service import CmirRunService

router = APIRouter(tags=["cmir"])


@router.post(
    "/cmir/email-events",
    response_model=Envelope[IngestEmailEventsResponse],
    status_code=202,
)
def create_email_events(
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
