"""API schemas for the fine-runs endpoint -- projection, projection
summary, mitigation options, and mitigation summary, chained in one call.

Domain-neutral (like app/api/v1/fine_runs.py): this orchestrates both
fine_projection and fine_mitigation, so it doesn't belong nested under
either domain's schema subpackage."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.fine_mitigation.mitigations import MitigationOptionsResponse
from app.schemas.fine_mitigation.summaries import MitigationSummaryStatusResponse
from app.schemas.fine_projection.projections import ProjectionResultResponse
from app.schemas.fine_projection.summaries import ProjectionSummaryStatusResponse


class OrderFineRunRequest(BaseModel):
    """Body for POST /orders/{order_id}/fine-runs -- projection, projection
    summary, mitigation options, and mitigation summary, chained in one call."""

    projection_date: date | None = None
    stacking_mode_override: Literal["SUM", "MAX"] | None = None
    force_regenerate_projection_summary: bool = False
    force_regenerate_mitigation_summary: bool = False


class OrderFineRunResponse(BaseModel):
    """Response for POST /orders/{order_id}/fine-runs."""

    projection: ProjectionResultResponse
    projection_summary: ProjectionSummaryStatusResponse
    mitigation_options: MitigationOptionsResponse
    mitigation_summary: MitigationSummaryStatusResponse
