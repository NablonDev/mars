"""Aggregates all v1 API routers under the /api/v1 prefix.

Every router below is included on ``protected_router``, which carries the
``require_internal_api_key`` dependency at construction time -- FastAPI
applies an ``APIRouter(dependencies=...)`` to every route nested under it
however deeply, so a new domain router only has to be added here to be
covered automatically. ``/health`` is the one deliberate exception: it is
included directly on the unprotected top-level ``router`` instead, so load
balancers/uptime monitors can reach it with no key.

``admin`` (seed/replay) stays a top-level module -- it spans both domains,
not either one exclusively. ``workflow_threads``/``processing_errors``/
``job_runs`` also stay top-level: ``workflow_thread``/``processing_error``/
``job_run`` are shared ``process``-schema resources used by both ``cmir`` and
``po_validation`` (and, for ``job_runs``, ``penalties``), not owned by any one
domain router.
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import require_internal_api_key
from app.api.v1 import (
    admin,
    cmir,
    health,
    internal,
    job_runs,
    po_validation,
    processing_errors,
    workflow_threads,
)
from app.api.v1.common import delivery_change_requests, fulfillment, master_data, purchase_orders
from app.api.v1.penalties import actual_penalties, mitigations, projections, rules

router = APIRouter()
router.include_router(health.router)

protected_router = APIRouter(dependencies=[Depends(require_internal_api_key)])
protected_router.include_router(cmir.router)
protected_router.include_router(internal.router)
protected_router.include_router(po_validation.router)
protected_router.include_router(workflow_threads.router)
protected_router.include_router(processing_errors.router)
protected_router.include_router(master_data.router)
protected_router.include_router(purchase_orders.router)
protected_router.include_router(fulfillment.router)
protected_router.include_router(delivery_change_requests.router)
protected_router.include_router(rules.router)
protected_router.include_router(projections.router)
protected_router.include_router(mitigations.router)
protected_router.include_router(actual_penalties.router)
protected_router.include_router(job_runs.router)
protected_router.include_router(admin.router)

router.include_router(protected_router)
