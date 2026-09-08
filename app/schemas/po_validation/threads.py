"""PO-validation snapshot shape for `GET /api/v1/workflow-threads/{thread_id}?include=snapshot`.

`SnapshotHistoryItem` is imported from the cmir package because `human_action` is shared.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.schemas.cmir.threads import IsoDatetime, SnapshotHistoryItem


class CandidateInfo(BaseModel):
    """Candidate substitute surfaced when a PO line's material is short or discontinued."""

    sap_material_number: str
    plant: str
    available_quantity: float
    shortfall: float
    suggested_substitute_material_code: str | None = None


class PoValidationThreadSnapshotResponse(BaseModel):
    """PO-validation-domain snapshot shape, returned by `PoValidationService.get_snapshot`."""

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
