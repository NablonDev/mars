"""API schemas for the `penalties` domain: rules, projections, mitigations,
delivery-change-requests, batch job-runs, and the seed/replay admin
endpoints.

Was the flat `app/schemas/fine_rules.py`, `fine_projection/*.py`,
`fine_mitigation/*.py`, `fine_runs.py`, `batches.py`, `admin.py`."""

from __future__ import annotations

from app.schemas.penalties.admin import (
    ScenarioDayResult,
    ScenarioSummary,
    SeedDataResponse,
    SimulateDailyRunResponse,
)
from app.schemas.penalties.batches import (
    JobItemListResponse,
    JobItemResponse,
    JobRunRequest,
    JobRunResponse,
    JobRunStatusCounts,
    JobRunStatusResponse,
)
from app.schemas.penalties.delivery_change_requests import (
    DeliveryChangeRequestCreate,
    DeliveryChangeRequestResponse,
    DeliveryChangeResponseRequest,
)
from app.schemas.penalties.mitigations import (
    MitigationOptionDetailResponse,
    MitigationOptionResponse,
    MitigationOptionsResponse,
    PenaltyMitigationSummaryRequest,
    PenaltyMitigationSummaryResponse,
    PenaltyMitigationSummaryStatusResponse,
)
from app.schemas.penalties.projections import (
    PenaltyExposureResponse,
    PenaltyProjectionDetailResponse,
    PenaltyProjectionHistoryRow,
    PenaltyProjectionResultResponse,
    PenaltyProjectionRunRequest,
    PenaltyProjectionSummaryRequest,
    PenaltyProjectionSummaryResponse,
    PenaltyProjectionSummaryStatusResponse,
    ViolationResponse,
)
from app.schemas.penalties.rules import PenaltyRuleRequest, PenaltyRuleResponse, PenaltyRuleTierSchema

__all__ = [
    "DeliveryChangeRequestCreate",
    "DeliveryChangeRequestResponse",
    "DeliveryChangeResponseRequest",
    "JobItemListResponse",
    "JobItemResponse",
    "JobRunRequest",
    "JobRunResponse",
    "JobRunStatusCounts",
    "JobRunStatusResponse",
    "MitigationOptionDetailResponse",
    "MitigationOptionResponse",
    "MitigationOptionsResponse",
    "PenaltyExposureResponse",
    "PenaltyMitigationSummaryRequest",
    "PenaltyMitigationSummaryResponse",
    "PenaltyMitigationSummaryStatusResponse",
    "PenaltyProjectionDetailResponse",
    "PenaltyProjectionHistoryRow",
    "PenaltyProjectionResultResponse",
    "PenaltyProjectionRunRequest",
    "PenaltyProjectionSummaryRequest",
    "PenaltyProjectionSummaryResponse",
    "PenaltyProjectionSummaryStatusResponse",
    "PenaltyRuleRequest",
    "PenaltyRuleResponse",
    "PenaltyRuleTierSchema",
    "ScenarioDayResult",
    "ScenarioSummary",
    "SeedDataResponse",
    "SimulateDailyRunResponse",
    "ViolationResponse",
]
