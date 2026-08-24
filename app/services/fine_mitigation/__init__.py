"""Ranks mitigation actions (speed up production, split shipment, faster
carrier) against accepting the projected fine as-is.

This package now holds both the pure calculation engine and the I/O-performing
orchestration built on top of it:
- Pure (no SQLAlchemy/FastAPI imports): `engine.py`, `types.py`.
- I/O-performing (DB/repository access): `service.py`, `summary.py`.
- Fixture data used only by seeding/tests, not the engine itself, lives
  in `app/services/seeding/scenario_data_mitigation.py`.

This module's own re-exports below cover only the pure engine surface.
"""

from app.services.fine_mitigation.engine import MitigationEngine
from app.services.fine_mitigation.types import MitigationInputs, MitigationOption, ShortageCause

__all__ = [
    "MitigationEngine",
    "MitigationInputs",
    "MitigationOption",
    "ShortageCause",
]
