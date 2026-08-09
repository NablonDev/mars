from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from cmir_agent.container import Container
from cmir_agent.schemas import (
    DecisionRequest,
    FieldsRequest,
    IngestEmailsRequest,
    IngestEmailsResponse,
    ProcessQueuedEmailRequest,
    RunsResponse,
    ThreadSnapshotResponse,
    ThreadStageResponse,
    UpdateDraftResponse,
)
from cmir_agent.services import CMIRRunService, ServiceError


def build_service() -> CMIRRunService:
    """Build the production API service from the project composition root."""
    container = Container.build()
    return CMIRRunService(
        email_reader=container.email_reader,
        graph=container.graph,
        agent_runs=container.agent_runs,
        workflow_threads=container.workflow_threads,
        pending_human_actions=container.pending_human_actions,
        hitl_actions=container.hitl_actions,
        hitl_state=container.hitl_state,
        email_repository=container.email_repository,
    )


def create_app(service: Optional[CMIRRunService] = None) -> FastAPI:
    """Create the FastAPI app for the PRD /api/v1 contract."""
    app = FastAPI(title="CMIR Resolution Agent API")
    app.state.service = service

    def get_service() -> CMIRRunService:
        if app.state.service is None:
            app.state.service = build_service()
        return app.state.service

    @app.on_event("startup")
    def startup() -> None:
        if app.state.service is None:
            app.state.service = build_service()

    @app.on_event("shutdown")
    def shutdown() -> None:
        Container.close()

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.post("/api/v1/ingest/emails", response_model=IngestEmailsResponse, status_code=202)
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

    @app.get("/api/v1/runs", response_model=RunsResponse)
    def list_runs(
        view: str = Query(default="threads"),
        batch_id: Optional[str] = None,
        agent_run_id: Optional[int] = None,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        sender: Optional[str] = None,
        limit: int = Query(default=50, ge=1, le=200),
        cursor: Optional[str] = None,
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

    @app.post("/api/v1/internal/process-email", response_model=ThreadStageResponse)
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

    @app.get("/api/v1/threads/{thread_id}/stage", response_model=ThreadStageResponse)
    def get_thread_stage(
        thread_id: str,
        run_service: CMIRRunService = Depends(get_service),
    ):
        return run_service.get_stage(thread_id)

    @app.get("/api/v1/threads/{thread_id}/snapshot", response_model=ThreadSnapshotResponse)
    def get_thread_snapshot(
        thread_id: str,
        run_service: CMIRRunService = Depends(get_service),
    ):
        return run_service.get_snapshot(thread_id)

    @app.post("/api/v1/threads/{thread_id}/missing-fields", response_model=ThreadStageResponse)
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

    @app.post("/api/v1/threads/{thread_id}/update", response_model=UpdateDraftResponse)
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

    @app.post("/api/v1/threads/{thread_id}/decision", response_model=ThreadStageResponse)
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

    return app


app = create_app()
