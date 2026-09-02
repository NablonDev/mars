"""API schemas for `penalties.mitigation_option` requests/responses and the
mitigation-summary trigger/poll contract.

Was `app/schemas/fine_mitigation/mitigations.py` + `summaries.py`."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.enums import SummaryStatus


class MitigationOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_order_id: UUID
    projection_date: date
    action: str | None
    projected_penalty_after: float
    action_cost: float
    net_saving: float
    risk_level: str
    confidence: str
    rationale: str | None


class PenaltyMitigationRunRequest(BaseModel):
    """Body for POST /penalties/mitigations. Accepts either a direct
    `(purchase_order_id, projection_date)` pair or a `projection_id` --
    resolved to that same pair via `_resolve_projection`
    (`app.api.v1.penalties.mitigations`), kept as a convenience alias, not
    the only way in anymore. Exactly one of the two shapes must be
    supplied."""

    projection_id: UUID | None = None
    purchase_order_id: UUID | None = None
    projection_date: date | None = None

    @model_validator(mode="after")
    def _validate_exactly_one_shape(self) -> PenaltyMitigationRunRequest:
        has_projection_id = self.projection_id is not None
        has_purchase_order_id = self.purchase_order_id is not None
        has_projection_date = self.projection_date is not None
        if has_purchase_order_id != has_projection_date:
            raise ValueError(
                "purchase_order_id and projection_date must both be provided together, or neither."
            )
        has_direct_pair = has_purchase_order_id and has_projection_date
        if has_projection_id == has_direct_pair:
            raise ValueError(
                "Provide exactly one of `projection_id` or (`purchase_order_id` + `projection_date`)."
            )
        return self


class PenaltyMitigationSummaryRequest(BaseModel):
    """Body for POST /penalties/mitigations/summary. `purchase_order_id`
    used to be a path param -- now that the route is flat, it travels in
    the body instead."""

    purchase_order_id: UUID
    as_of_date: date | None = None
    force_regenerate: bool = False


class PenaltyMitigationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    as_of_date: date
    prompt_version: str
    model_name: str
    summary: str
    is_reused: bool = False
    generated_for_date: date | None = None
    unchanged_since: date | None = None
    unchanged_for_days: int | None = None


class MitigationOptionDetailResponse(MitigationOptionResponse):
    """GET /penalties/mitigations/{mitigation_id}?include=summary -- pure
    read, never schedules generation. Also the `options` element shape for
    `GET /penalties/mitigations?include=summary` (the list route) and
    `POST /penalties/mitigations?include=summary` (the run route) -- same
    optional-nested-field pattern, applied per list item instead of to a
    single resource."""

    summary_status: SummaryStatus | None = None
    summary: PenaltyMitigationSummaryResponse | None = None


class MitigationOptionsResponse(BaseModel):
    purchase_order_id: UUID
    projection_date: date
    options: list[MitigationOptionDetailResponse]


class PenaltyMitigationSummaryStatusResponse(BaseModel):
    """`status`/`as_of_date` are `None` only for the genuine "never
    requested" case -- see `PenaltyProjectionSummaryStatusResponse`'s
    docstring, its exact mirror."""

    purchase_order_id: UUID
    as_of_date: date | None
    status: SummaryStatus | None
    summary: PenaltyMitigationSummaryResponse | None = None
    error_message: str | None = None
