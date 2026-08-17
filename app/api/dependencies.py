from __future__ import annotations

from fastapi import Request

from app.core.container import Container
from app.services.cmir_run_service import CMIRRunService
from app.services.po_validation_service import PoValidationService


def build_service() -> CMIRRunService:
    """Build the production CMIR service from the project composition root."""
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
        cmir_repository=container.cmir_repository,
    )


def build_po_validation_service() -> PoValidationService:
    """Build the production PO Validation service from the project composition root."""
    container = Container.build()
    return PoValidationService(
        graph=container.po_validation_graph,
        po_lines=container.po_lines,
        material_master=container.material_master,
        po_line_errors=container.po_line_errors,
        agent_runs=container.agent_runs,
        workflow_threads=container.workflow_threads,
        pending_human_actions=container.pending_human_actions,
        hitl_actions=container.hitl_actions,
        hitl_state=container.hitl_state,
    )


def get_service(request: Request) -> CMIRRunService:
    """FastAPI dependency returning the app-instance-lifetime CMIR service.

    Memoized on `request.app.state` (not a plain lru_cache) so each FastAPI
    app instance -- including a test-created one that already carries a fake
    via create_app(service=...) -- gets its own singleton instead of sharing
    one across the process.
    """
    if request.app.state.service is None:
        request.app.state.service = build_service()
    return request.app.state.service


def get_po_service(request: Request) -> PoValidationService:
    """FastAPI dependency returning the app-instance-lifetime PO Validation service."""
    if request.app.state.po_service is None:
        request.app.state.po_service = build_po_validation_service()
    return request.app.state.po_service
