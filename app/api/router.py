"""Aggregates every v1 router into one -- main.py mounts this once,
under /api/v1, instead of including each router file individually."""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    explanations,
    facts,
    fine_rules,
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
router.include_router(explanations.router)
router.include_router(admin.router)
