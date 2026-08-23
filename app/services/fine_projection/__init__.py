"""Deterministic engine for calculating shortage, delay, and projected fines.

This package now holds both the pure calculation engine and the I/O-performing
orchestration built on top of it:
- Pure (no SQLAlchemy/FastAPI imports): `types.py`, `engine.py`,
  `shortage.py`, `delay.py`.
- I/O-performing (DB/repository access): `service.py`, `summary.py`.
- Fixture data used only by seeding/tests, not the engine itself, lives
  in `app/services/seeding/scenario_data_projection.py`.

This module's own re-exports below cover only the pure engine surface.
"""

from app.services.fine_projection.delay import (
    compute_delay_probability,
    price_delay_fine,
    resolve_expected_ship_date,
)
from app.services.fine_projection.engine import ProjectionEngine
from app.services.fine_projection.shortage import (
    SHORTAGE_LOCKED_IN_PROBABILITY,
    compute_shortage_probability,
    price_shortage_fine,
    shortfall_units_for_pricing,
)
from app.services.fine_projection.types import (
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
    "ProjectionEngine",
    "ProjectionResult",
    "ViolationProjection",
    "compute_delay_probability",
    "compute_shortage_probability",
    "price_delay_fine",
    "price_shortage_fine",
    "resolve_expected_ship_date",
    "shortfall_units_for_pricing",
]
