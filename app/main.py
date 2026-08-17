from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.dependencies import build_po_validation_service, build_service
from app.api.router import create_api_router
from app.core.container import Container
from app.core.exceptions import ServiceError
from app.services.cmir_run_service import CMIRRunService
from app.services.po_validation_service import PoValidationService


def create_app(
    service: Optional[CMIRRunService] = None,
    po_service: Optional[PoValidationService] = None,
) -> FastAPI:
    """Create the FastAPI app for the PRD /api/v1 contract (CMIR + PO Validation)."""
    app = FastAPI(title="CMIR Resolution Agent API")
    app.state.service = service
    app.state.po_service = po_service

    app.include_router(create_api_router())

    @app.on_event("startup")
    def startup() -> None:
        if app.state.service is None:
            app.state.service = build_service()
        if app.state.po_service is None:
            app.state.po_service = build_po_validation_service()

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

    return app


app = create_app()
