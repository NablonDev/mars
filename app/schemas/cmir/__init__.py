"""`app.schemas.cmir` -- was the flat `app/schemas/cmir.py`, split per the
approved plan §2/§6 into `domain.py` (graph-state/value-object shapes),
`email_events.py`, and `threads.py`.

Re-exports the graph-state/value-object names so existing, out-of-scope-
this-phase call sites keep working unchanged:
`app/agents/cmir/nodes.py`, `app/services/cmir/{validation,merge,extractor,
run_service}.py`, and `app/services/email_reader.py` all do
`from app.schemas.cmir import Cmir, ...`.
"""

from __future__ import annotations

from app.schemas.cmir.domain import (
    CMIR_CONTENT_FIELDS,
    MANDATORY_FIELDS,
    Cmir,
    CmirStatus,
    EmailMessage,
)

__all__ = [
    "CMIR_CONTENT_FIELDS",
    "MANDATORY_FIELDS",
    "Cmir",
    "CmirStatus",
    "EmailMessage",
]
