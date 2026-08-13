"""Aggregates all v1 API routers under the /api/v1 prefix."""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    facts,
    fine_rules,
    fine_summaries,
    health,
    master_data,
    orders,
    projections,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(master_data.router)
router.include_router(fine_rules.router)
router.include_router(orders.router)
router.include_router(facts.router)
router.include_router(projections.router)
router.include_router(fine_summaries.router)
router.include_router(admin.router)
