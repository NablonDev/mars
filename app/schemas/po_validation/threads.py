"""PO-validation-domain snapshot shape for
`GET /api/v1/workflow-threads/{thread_id}?include=snapshot` (was
`GET /threads/{id}/stage` + `GET /threads/{id}/snapshot`, PRD §11.3).

Imports `SnapshotHistoryItem` from `app.schemas.cmir.threads` -- the same
cross-domain import the pre-Phase-7b `app/schemas/po_validation.py` already
made (`human_action` is a shared `process`-schema table, so its history-row
shape is not domain-specific).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.schemas.cmir.threads import IsoDatetime, SnapshotHistoryItem


class CandidateInfo(BaseModel):
    sap_material_number: str
    plant: str
    available_quantity: float
    shortfall: float
    suggested_substitute_material_code: str | None = None


class PoValidationThreadSnapshotResponse(BaseModel):
    """`PoValidationService.get_snapshot`'s real return shape -- structurally
    different from `app.schemas.cmir.threads.CmirThreadSnapshotResponse`
    (PRD §10.4 vs §11.3); see `app/api/v1/workflow_threads.py` for the
    domain-dispatch this forces."""

    agent_run_id: UUID
    thread_id: str
    po_line_id: str
    po_number: str | None = None
    po_line_number: str
    customer_material_code: str
    order_quantity: float
    stage: str
    candidate: CandidateInfo | None = None
    editable_fields: list[str]
    history: list[SnapshotHistoryItem]
    updated_at: IsoDatetime
