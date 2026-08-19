"""Deterministic engine for calculating shortage, delay, and projected fines."""

from app.services.fine_projection.delay import (
    compute_delay_probability,
    price_delay_fine,
    resolve_expected_ship_date,
)
from app.services.fine_projection.models import (
    DELAY_VIOLATION_TYPES,
    SHORTAGE_VIOLATION_TYPES,
    AppointmentStatus,
    CalcType,
    FineRule,
    FineTier,
    OrderSnapshot,
    ProductionStatus,
    ProjectionResult,
    ViolationProjection,
)
from app.services.fine_projection.orchestrator import project_order
from app.services.fine_projection.shortage import (
    SHORTAGE_LOCKED_IN_PROBABILITY,
    compute_shortage_probability,
    price_shortage_fine,
    shortfall_units_for_pricing,
)

__all__ = [
    "DELAY_VIOLATION_TYPES",
    "SHORTAGE_LOCKED_IN_PROBABILITY",
    "SHORTAGE_VIOLATION_TYPES",
    "AppointmentStatus",
    "CalcType",
    "FineRule",
    "FineTier",
    "OrderSnapshot",
    "ProductionStatus",
    "ProjectionResult",
    "ViolationProjection",
    "compute_delay_probability",
    "compute_shortage_probability",
    "price_delay_fine",
    "price_shortage_fine",
    "project_order",
    "resolve_expected_ship_date",
    "shortfall_units_for_pricing",
]
