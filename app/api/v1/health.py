"""
Readiness check. Actually touches Postgres -- a liveness check that only
proves the Python process is up tells an operator nothing they didn't
already know from the TCP connect.
"""

import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from app.api.dependencies import get_database
from app.db.session import Database
from app.schemas.common import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
def health(response: Response, database: Database = Depends(get_database)) -> HealthResponse:
    try:
        # `with` is what returns the connection to the pool on every call,
        # including the failure path. Never `dispose()` here -- that tears
        # down the whole pool and is strictly an app-shutdown operation
        # (app/main.py's `lifespan`).
        with database.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    # Deliberately broad: a health check must report "degraded", never itself
    # raise. `OperationalError`, a DNS failure and a bad DSN are all the same
    # answer to the caller.
    except Exception:
        # Traceback to the log (redacted by app/core/logging.py, which
        # scrubs DSN credentials out of driver errors); the response body
        # says only "unreachable". Never surface the driver message: it can
        # carry the host, port and connection string.
        logger.warning("database health check failed", exc_info=True)
        response.status_code = 503
        return HealthResponse(status="degraded", database="unreachable")

    return HealthResponse(status="ok", database="ok")
