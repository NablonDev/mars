from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_po_service, get_service
from app.core.exceptions import ServiceError
from app.schemas.cmir import (
    DecisionRequest,
    FieldsRequest,
    IngestEmailsRequest,
    IngestEmailsResponse,
    ProcessQueuedEmailRequest,
    RunsResponse,
    ThreadStageResponse,
    UpdateDraftResponse,
)
from app.services.cmir_run_service import CMIRRunService
from app.services.po_validation_service import PoValidationService


def create_router() -> APIRouter:
    """Build the CMIR routes (PRD API contract)."""
    # No prefix here: app/main.py mounts the aggregated v1 router under /api/v1 once.
    router = APIRouter(tags=["cmir"])

    @router.post("/ingest/emails", response_model=IngestEmailsResponse, status_code=202)
    def ingest_emails(
        request_body: IngestEmailsRequest,
        run_service: CMIRRunService = Depends(get_service),
    ):
        if request_body.source != "gmail":
            raise ServiceError(
                "VALIDATION_ERROR",
                "Only gmail source is currently configured.",
                status_code=422,
                details={"source": request_body.source},
            )
        return run_service.start_email_ingest(
            max_workers=request_body.max_workers,
            subject_contains=request_body.filters.subject_contains,
            unread_only=request_body.filters.unread_only,
        )

    @router.get("/runs", response_model=RunsResponse)
    def list_runs(
        view: str = Query(default="threads"),
        batch_id: str | None = None,
        agent_run_id: UUID | None = None,
        status: str | None = None,
        stage: str | None = None,
        sender: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = None,
        run_service: CMIRRunService = Depends(get_service),
    ):
        return run_service.list_runs(
            view=view,
            batch_id=batch_id,
            agent_run_id=agent_run_id,
            status=status,
            stage=stage,
            sender=sender,
            limit=limit,
            cursor=cursor,
        )

    @router.post("/internal/process-email", response_model=ThreadStageResponse)
    def process_queued_email(
        request_body: ProcessQueuedEmailRequest,
        run_service: CMIRRunService = Depends(get_service),
    ):
        return run_service.process_queued_email(
            batch_id=request_body.batch_id,
            email=request_body.email,
            queue_message_id=request_body.queue_message_id,
            email_id=request_body.email_id,
        )

    @router.get("/threads/{thread_id}/stage", response_model=ThreadStageResponse)
    def get_thread_stage(
        thread_id: str,
        run_service: CMIRRunService = Depends(get_service),
    ):
        return run_service.get_stage(thread_id)

    @router.get("/threads/{thread_id}/snapshot", response_model=None)
    def get_thread_snapshot(
        thread_id: str,
        run_service: CMIRRunService = Depends(get_service),
        po_run_service: PoValidationService = Depends(get_po_service),
    ):
        # workflow_threads is shared across both agents. PoValidationService.get_snapshot
        # raises THREAD_NOT_FOUND both for an unknown thread_id and for a thread that
        # belongs to the CMIR agent (po_line_id is None there), so trying PO first and
        # falling back to CMIR correctly dispatches without any direct Container access
        # here. CMIR and PO Validation snapshots have different shapes (PRD §10.4 vs
        # §11.3), so this route intentionally has no single fixed response_model.
        try:
            return po_run_service.get_snapshot(thread_id)
        except ServiceError as exc:
            if exc.code != "THREAD_NOT_FOUND":
                raise
        return run_service.get_snapshot(thread_id)

    @router.post("/threads/{thread_id}/missing-fields", response_model=ThreadStageResponse)
    def submit_missing_fields(
        thread_id: str,
        request_body: FieldsRequest,
        run_service: CMIRRunService = Depends(get_service),
    ):
        return run_service.submit_missing_fields(
            thread_id,
            actor=request_body.actor,
            fields=request_body.fields,
            expected_updated_at=request_body.expected_updated_at,
        )

    @router.post("/threads/{thread_id}/update", response_model=UpdateDraftResponse)
    def update_draft(
        thread_id: str,
        request_body: FieldsRequest,
        run_service: CMIRRunService = Depends(get_service),
    ):
        return run_service.update_draft(
            thread_id,
            actor=request_body.actor,
            fields=request_body.fields,
            expected_updated_at=request_body.expected_updated_at,
        )

    @router.post("/threads/{thread_id}/decision", response_model=ThreadStageResponse)
    def submit_decision(
        thread_id: str,
        request_body: DecisionRequest,
        run_service: CMIRRunService = Depends(get_service),
    ):
        return run_service.submit_decision(
            thread_id,
            actor=request_body.actor,
            decision=request_body.decision,
            reason=request_body.reason,
            expected_updated_at=request_body.expected_updated_at,
        )

    return router
