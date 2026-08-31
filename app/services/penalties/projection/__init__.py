"""Deterministic engine for calculating shortage, delay, and projected penalties.

Moved from `app/services/fine_projection/` (Phase 3 -- services move/
folder-split; see that package's former `__init__.py` for the pre-move
docstring, preserved in spirit below).

This package holds both the pure calculation engine and the I/O-performing
orchestration built on top of it:
- Pure (no SQLAlchemy/FastAPI imports): `types.py`, `engine.py`,
  `shortage.py`, `delay.py`.
- I/O-performing (DB/repository access): `service.py`, `summary_service.py`.
- Fixture data used only by seeding/tests, not the engine itself, lives
  in `app/services/seeding/scenario_data_projection.py`.

This module's re-exports below cover both the pure engine surface and the
I/O-performing summary service (`summary_service.py`'s own `__all__` was
merged in here -- architecture review). The `.summary_service` import is
deliberately last: `summary_service.py` itself does
`from app.services.penalties.projection import DELAY_VIOLATION_TYPES,
SHORTAGE_VIOLATION_TYPES` at module level, which resolves fine against this
partially-initialized package module only because those two names are
already bound above by the time that import runs.
"""

from app.services.penalties.projection.delay import (  # noqa: I001 -- order is deliberate, not isort's; see module docstring
    compute_delay_probability,
    price_delay_penalty,
    resolve_expected_ship_date,
)
from app.services.penalties.projection.engine import ProjectionEngine
from app.services.penalties.projection.shortage import (
    SHORTAGE_LOCKED_IN_PROBABILITY,
    compute_shortage_probability,
    price_shortage_penalty,
    shortfall_units_for_pricing,
)
from app.services.penalties.projection.types import (
    DELAY_VIOLATION_TYPES,
    SHORTAGE_VIOLATION_TYPES,
    AppointmentStatus,
    CalcType,
    OrderSnapshot,
    PenaltyRule,
    PenaltyRuleTier,
    ProductionStatus,
    ProjectionResult,
    ViolationProjection,
)
from app.services.penalties.projection.summary_service import (
    PenaltyProjectionSummaryOutputWithReuse,
    ProjectionSummaryService,
    SummaryJob,
)

__all__ = [
    "DELAY_VIOLATION_TYPES",
    "SHORTAGE_LOCKED_IN_PROBABILITY",
    "SHORTAGE_VIOLATION_TYPES",
    "AppointmentStatus",
    "CalcType",
    "OrderSnapshot",
    "PenaltyProjectionSummaryOutputWithReuse",
    "PenaltyRule",
    "PenaltyRuleTier",
    "ProductionStatus",
    "ProjectionEngine",
    "ProjectionResult",
    "ProjectionSummaryService",
    "SummaryJob",
    "ViolationProjection",
    "compute_delay_probability",
    "compute_shortage_probability",
    "price_delay_penalty",
    "price_shortage_penalty",
    "resolve_expected_ship_date",
    "shortfall_units_for_pricing",
]
