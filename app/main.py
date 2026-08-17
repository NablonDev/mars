"""FastAPI application factory and application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.dependencies import build_po_validation_service, build_service
from app.api.router import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.container import Container
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import AccessLogMiddleware, RequestIdMiddleware
from app.db.session import Database
from app.services.cmir_run_service import CMIRRunService
from app.services.po_validation_service import PoValidationService


def create_app(
    service: CMIRRunService | None = None,
    po_service: PoValidationService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        """One engine/connection pool per process; disposed on shutdown."""
        app.state.database = Database(
            resolved.database_url,
            pool_size=resolved.db_pool_size,
            max_overflow=resolved.db_max_overflow,
            pool_timeout=resolved.db_pool_timeout,
        )
        # CMIR/PO-validation services carry their own composition root
        # (LangGraph + a shared Postgres checkpointer) -- built here unless a
        # test already injected a fake via create_app(service=...).
        if app.state.service is None:
            app.state.service = build_service()
        if app.state.po_service is None:
            app.state.po_service = build_po_validation_service()
        try:
            yield
        finally:
            app.state.database.dispose()
            Container.close()

    app = FastAPI(
        title=resolved.project_name,
        version=resolved.version,
        lifespan=lifespan,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url="/redoc" if resolved.docs_enabled else None,
        openapi_url="/openapi.json" if resolved.docs_enabled else None,
    )
    app.state.service = service
    app.state.po_service = po_service

    # Last-added middleware is outermost; request IDs must wrap access logging.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(api_v1_router, prefix="/api/v1")
    register_exception_handlers(app)

    return app


app = create_app()
