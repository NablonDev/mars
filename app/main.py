"""
FastAPI application factory. Builds the one `Database` instance in a
`lifespan` context manager and stashes it on `app.state.database`
(app/api/dependencies.py::get_database reads it from there), disposing its
connection pool on shutdown; configures structured logging; installs the
request-id and access-log middleware; registers the v1 router; and maps
domain exceptions to one JSON error contract via
app/core/exceptions.py::register_exception_handlers, rather than
scattering try/except-to-HTTPException across every route.

Run with: uvicorn app.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import AccessLogMiddleware, RequestIdMiddleware
from app.db.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """One engine (and therefore one connection pool) per process,
        torn down on shutdown. Building it here rather than at import
        time keeps `create_app()` free of I/O side effects, and `dispose()`
        stops a reloading/redeploying process from leaking server-side
        connections."""
        app.state.database = Database(
            resolved.database_url,
            pool_size=resolved.db_pool_size,
            max_overflow=resolved.db_max_overflow,
            pool_timeout=resolved.db_pool_timeout,
        )
        try:
            yield
        finally:
            app.state.database.dispose()

    app = FastAPI(title=resolved.api_title, version=resolved.api_version, lifespan=lifespan)

    # Added innermost-first: `add_middleware` prepends, so the last one
    # added is the outermost. RequestIdMiddleware has to wrap the access
    # log, or the access line has no request id to report.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(api_v1_router, prefix="/api/v1")
    register_exception_handlers(app)

    return app


app = create_app()
