"""FastAPI application factory and application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import AccessLogMiddleware, RequestIdMiddleware
from app.db.session import Database
from app.queue.factory import build_job_queue


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        """Create process-wide DB and queue resources and dispose them on shutdown."""
        app.state.database = Database(
            resolved.database_url,
            pool_size=resolved.db_pool_size,
            max_overflow=resolved.db_max_overflow,
            pool_timeout=resolved.db_pool_timeout,
        )
        app.state.job_queue = build_job_queue(resolved, app.state.database)
        try:
            yield
        finally:
            dispatcher, _source = app.state.job_queue
            dispatcher.close()
            app.state.database.dispose()

    app = FastAPI(
        title=resolved.project_name,
        version=resolved.version,
        lifespan=lifespan,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url="/redoc" if resolved.docs_enabled else None,
        openapi_url="/openapi.json" if resolved.docs_enabled else None,
    )

    # Last-added middleware is outermost; request IDs must wrap access logging.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(api_v1_router, prefix="/api/v1")
    register_exception_handlers(app)

    return app


app = create_app()
