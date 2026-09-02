"""API schemas for `penalties.penalty_projection` requests/responses and the
projection-summary trigger/poll contract.

Was `app/schemas/fine_projection/projections.py` + `summaries.py`. Field
names drop the stale `fine`/`order` vocabulary (`projected_fine_amount` ->
`expected_penalty_amount`, matching `PenaltyProjectionRepository`'s own
dict shape; `order_id` -> `purchase_order_id`); the pure-calc engine's
dataclasses themselves ARE now renamed to match (see
`app.services.penalties.projection.types`'s module docstring) -- both
layers share `penalty_amount`/`expected_penalty_amount` field-for-field.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import SummaryStatus
from app.schemas.penalties.mitigations import MitigationOptionResponse, PenaltyMitigationSummaryResponse


class PenaltyProjectionRunRequest(BaseModel):
    """Body for POST /penalties/projections. `purchase_order_id` used to be
    a path param (`POST /purchase-orders/{purchase_order_id}/penalty-
    projections`) -- now that the route is flat, it travels in the body
    instead."""

    purchase_order_id: UUID
    projection_date: date | None = None
    stacking_mode_override: Literal["SUM", "MAX"] | None = None


class ViolationResponse(BaseModel):
    # The persisted `penalties.penalty_projection` row this violation was
    # just written to -- one row per violation (see
    # `PenaltyProjectionRepository.save_result`), so this is the one id a
    # client needs to call `GET /penalties/projections/{projection_id}` or
    # `POST /penalties/mitigations` (`projection_id` in the body) against
    # this run's output without a separate list/query round-trip. Named to
    # match those two routes' own `projection_id` param, not the bare `id`
    # field `PenaltyProjectionHistoryRow` (the GET-by-id response shape)
    # uses for the same column.
    projection_id: UUID
    violation_type: str
    rule_id: str
    probability: float
    penalty_amount: float
    expected_penalty_amount: float


class PenaltyProjectionResultResponse(BaseModel):
    """One projection run's engine output -- POST response shape.

    `summary_status`/`summary`/`mitigations`/`mitigation_summary_status`/
    `mitigation_summary` are the same optional `?include=` pure-read fields
    `PenaltyProjectionDetailResponse` carries (see that model's docstring) --
    populated only when `POST /penalties/projections` is called with the
    matching `?include=` value, and only from cached state; the run itself
    never schedules summary generation nor computes mitigations as a side
    effect.
    """

    purchase_order_id: UUID
    projection_date: date
    days_to_delivery: int
    shortage_probability: float
    delay_probability: float
    violations: list[ViolationResponse]
    total_expected_penalty_amount: float
    stacking_mode: str
    summary_status: SummaryStatus | None = None
    summary: PenaltyProjectionSummaryResponse | None = None
    mitigations: list[MitigationOptionResponse] | None = None
    mitigation_summary_status: SummaryStatus | None = None
    mitigation_summary: PenaltyMitigationSummaryResponse | None = None


class PenaltyProjectionHistoryRow(BaseModel):
    """One persisted `penalty_projection` row -- GET history / cross-PO
    open-list / single-projection-read shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_order_id: UUID
    rule_id: UUID
    projection_date: date
    violation_type: str
    failure_probability: float
    penalty_amount: float
    expected_penalty_amount: float
    days_to_delivery: int
    projection_status: str


class PenaltyExposureResponse(BaseModel):
    purchase_order_id: UUID
    projection_date: date
    total_expected_penalty_amount: float
    violations: list[PenaltyProjectionHistoryRow]


class PenaltyProjectionSummaryRequest(BaseModel):
    """Body for POST /penalties/projections/summary. `purchase_order_id`
    used to be a path param -- now that the route is flat, it travels in
    the body instead."""

    purchase_order_id: UUID
    as_of_date: date | None = None
    force_regenerate: bool = False


class PenaltyProjectionSummaryResponse(BaseModel):
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


class PenaltyProjectionSummaryStatusResponse(BaseModel):
    """`status`/`as_of_date` are `None` only for the genuine "never
    requested" case -- no projection-summary job has ever been posted for
    this `purchase_order_id` (at any date, in the direct-lookup sense this
    route uses). This matches the `?include=summary` convention elsewhere
    (`summary_status=None` is a normal, non-error state, never a 404) --
    see `get_penalty_projection_summary`'s docstring for why this dedicated
    route converged to the same rule."""

    purchase_order_id: UUID
    as_of_date: date | None
    status: SummaryStatus | None
    summary: PenaltyProjectionSummaryResponse | None = None
    error_message: str | None = None


class PenaltyProjectionDetailResponse(PenaltyProjectionHistoryRow):
    """`?include=` shape -- pure read, never schedules generation. Used by
    every `GET`/`POST /penalties/projections...` route: the single-resource
    `GET /penalties/projections/{projection_id}`, the flat/cross-PO
    `GET /penalties/projections?purchase_order_id=&status=&...` list, and
    (via `PenaltyProjectionResultResponse`'s sibling fields) the run
    endpoint `POST /penalties/projections`. All three share the same
    `{"summary", "mitigations", "mitigation_summary"}` allow-list -- no
    route gets a narrower one.

    `summary_status` is always populated once `include=summary` is
    requested (`READY`/`PENDING`/`FAILED`, from cached state only);
    `summary` itself is populated only when `summary_status == READY`.
    `mitigations` is populated only when at least one mitigation option has
    been computed for this row's `(purchase_order_id, projection_date)`;
    `mitigation_summary_status`/`mitigation_summary` follow the identical
    READY/PENDING/FAILED-from-cache convention as `summary_status`/`summary`,
    against the mitigation-summary job instead of the projection-summary job.
    """

    summary_status: SummaryStatus | None = None
    summary: PenaltyProjectionSummaryResponse | None = None
    mitigations: list[MitigationOptionResponse] | None = None
    mitigation_summary_status: SummaryStatus | None = None
    mitigation_summary: PenaltyMitigationSummaryResponse | None = None
