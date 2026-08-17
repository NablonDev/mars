from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.cmir import create_router as create_cmir_router
from app.api.v1.po_validation import create_router as create_po_validation_router


def create_api_router() -> APIRouter:
    """Aggregate all /api/v1 routers into one router for the app to mount."""
    router = APIRouter()
    router.include_router(create_cmir_router())
    router.include_router(create_po_validation_router())
    return router
