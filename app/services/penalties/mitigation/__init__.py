"""Ranks mitigation actions (speed up production, split shipment, faster
carrier) against accepting the projected penalty as-is.

Moved from `app/services/fine_mitigation/` (Phase 3 -- services move/
folder-split).

This package holds both the pure calculation engine and the I/O-performing
orchestration built on top of it:
- Pure (no SQLAlchemy/FastAPI imports): `engine.py`, `types.py`.
- I/O-performing (DB/repository access): `service.py`, `summary_service.py`.
- Fixture data used only by seeding/tests, not the engine itself, lives
  in `app/services/seeding/scenario_data_mitigation.py`.

This module's re-exports below cover both the pure engine surface and the
I/O-performing summary service (`summary_service.py`'s own `__all__` was
merged in here -- architecture review). The summary-service names are
re-exported lazily (`__getattr__`, PEP 562) rather than imported eagerly at
the top of this file: `summary_service.py` imports back
`app.repositories.penalties.mitigation`, which itself does
`from app.services.penalties.mitigation.types import ...` at module level.
Any module-level import of `.summary_service` here would force that
repository module's own (possibly still-executing) import to resume through
this partially-initialized package whenever something reaches `.types`/
`.engine` via the repository first -- a genuine circular-import failure,
not a hypothetical one (it broke `pytest` collection during this pass).
Deferring the summary_service import to first attribute access avoids it
while keeping the same `from app.services.penalties.mitigation import
MitigationSummaryService` surface for every other caller.
"""

from app.services.penalties.mitigation.engine import MitigationEngine
from app.services.penalties.mitigation.types import MitigationInputs, MitigationOption, ShortageCause

__all__ = [
    "MitigationEngine",
    "MitigationInputs",
    "MitigationOption",
    "MitigationSummaryService",
    "PenaltyMitigationSummaryOutputWithReuse",
    "ShortageCause",
    "SummaryJob",
]

_LAZY_SUMMARY_SERVICE_EXPORTS = frozenset(
    {"MitigationSummaryService", "PenaltyMitigationSummaryOutputWithReuse", "SummaryJob"}
)


def __getattr__(name: str):
    if name in _LAZY_SUMMARY_SERVICE_EXPORTS:
        from app.services.penalties.mitigation import summary_service

        return getattr(summary_service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
