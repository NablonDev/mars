"""
Mars Petcare -- Projected Fines System
Deterministic fine projection engine.

Given an order's state as of a specific day (production status, confirmed
quantity, shipment/appointment status), this package computes:
  - shortage failure probability (shortage.py)
  - delay failure probability (delay.py)
  - the fine-if-realized and expected fine for each applicable rule,
    and the total expected fine for that day (orchestrator.py)

Pure functions/dataclasses only -- same input always produces the same
output. No I/O, no database access, no randomness, no SQLAlchemy or
FastAPI imports anywhere under this package. A caller (a service, a
script) is responsible for assembling the OrderSnapshot for "today" from
SAP/Datalliance/portal data and for persisting the ProjectionResult.

Every constant in shortage.py/delay.py (score bands, the 5% floor, the
buffer/stage table, anticipated-shortfall assumptions) is a calibration
placeholder documented in docs/FINE_ENGINE.md. Replace them once real
outcome history exists in fact_actual_fine.

This is the public surface. Internal helpers (`_gap_points`,
`_score_to_probability`, etc.) are intentionally not re-exported here --
import them from `app.engine.shortage` / `app.engine.delay` directly if a
test needs one.
"""

from app.engine.delay import compute_delay_probability, price_delay_fine, resolve_expected_ship_date
from app.engine.models import (
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
from app.engine.orchestrator import project_order
from app.engine.shortage import (
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
