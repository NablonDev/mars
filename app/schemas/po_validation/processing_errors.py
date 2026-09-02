"""API schemas for `GET /api/v1/processing-errors?purchase_order_line_id={id}`
(was `GET /po-lines/{id}/errors`).

`process.processing_error` generalizes the old `po_line_errors` table and is
shared across domains (approved plan §2/§6) -- placed under
`app.schemas.po_validation` rather than a new top-level `process` schemas
package since `PoValidationService.get_errors` is this phase's only actual
caller; revisit if/when `penalties` grows its own error-listing route.

Flagged, not silently smoothed over: this response has no
"suggested substitute material" field. `process.processing_error` carries no
material/plant reference (only `job_item_id`/`agent_run_id`/
`purchase_order_line_id`), so resolving `MaterialMaster.follow_up_material_id`
into a substitute `sap_material_number` -- the still-open item from the
earlier cmir/po_validation `nodes.py` repair -- has no natural slot on
*this* response shape. `app/repositories/` is read-only this phase, so no
`MasterDataRepository` query method was added; see this phase's report.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ProcessingErrorItem(BaseModel):
    id: UUID
    job_item_id: UUID | None = None
    agent_run_id: UUID | None = None
    purchase_order_line_id: UUID | None = None
    error_type: str
    error_code: str | None = None
    error_message: str | None = None
    node_name: str | None = None
    occurred_at: datetime | None = None
    resolved: bool = False
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class ProcessingErrorsListResponse(BaseModel):
    items: list[ProcessingErrorItem]
