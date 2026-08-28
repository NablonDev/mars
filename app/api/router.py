"""Aggregates all v1 API routers under the /api/v1 prefix.

Every router below is included on ``protected_router``, which carries the
``require_internal_api_key`` dependency at construction time -- FastAPI
applies an ``APIRouter(dependencies=...)`` to every route nested under it
however deeply, so a new domain router only has to be added here to be
covered automatically. ``/health`` is the one deliberate exception: it is
included directly on the unprotected top-level ``router`` instead, so load
balancers/uptime monitors can reach it with no key.
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import require_internal_api_key
from app.api.v1 import (
    admin,
    batches,
    fine_master_data,
    fine_rules,
    fine_runs,
    health,
    orders,
)
from app.api.v1.cmir import create_router as create_cmir_router
from app.api.v1.fine_mitigation import mitigations
from app.api.v1.fine_mitigation import summaries as fine_mitigation_summaries
from app.api.v1.fine_projection import facts, po_delivery_change_requests, projections
from app.api.v1.fine_projection import summaries as fine_projection_summaries
from app.api.v1.po_validation import create_router as create_po_validation_router

router = APIRouter()
router.include_router(health.router)

protected_router = APIRouter(dependencies=[Depends(require_internal_api_key)])
protected_router.include_router(create_cmir_router())
protected_router.include_router(create_po_validation_router())
protected_router.include_router(fine_master_data.router)
protected_router.include_router(fine_rules.router)
protected_router.include_router(orders.router)
protected_router.include_router(facts.router)
protected_router.include_router(projections.router)
protected_router.include_router(po_delivery_change_requests.router)
protected_router.include_router(fine_projection_summaries.router)
protected_router.include_router(mitigations.router)
protected_router.include_router(fine_mitigation_summaries.router)
protected_router.include_router(fine_runs.router)
protected_router.include_router(batches.router)
protected_router.include_router(admin.router)

router.include_router(protected_router)
