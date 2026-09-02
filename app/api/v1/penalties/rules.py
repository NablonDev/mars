"""API endpoints for managing `penalties.penalty_rule`."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_penalty_rule_repository
from app.core.envelope import Envelope, success_envelope
from app.repositories.penalties.rule import PenaltyRuleRepository
from app.schemas.penalties.rules import PenaltyRuleRequest, PenaltyRuleResponse

router = APIRouter(tags=["penalty-rules"])


@router.post(
    "/penalties/rules", response_model=Envelope[PenaltyRuleResponse], status_code=status.HTTP_201_CREATED
)
def create_penalty_rule(
    body: PenaltyRuleRequest,
    rules: PenaltyRuleRepository = Depends(get_penalty_rule_repository),
) -> Envelope[PenaltyRuleResponse]:
    tiers = [t.model_dump() for t in body.tiers] if body.tiers else None
    created = rules.add_rule(
        rule_code=body.rule_code,
        retailer_id=body.retailer_id,
        violation_type=body.violation_type,
        calc_type=body.calc_type,
        rate=body.rate,
        threshold_pct=body.threshold_pct,
        cap_amount=body.cap_amount,
        grace_period_days=body.grace_period_days,
        effective_start_date=body.effective_start_date,
        effective_end_date=body.effective_end_date,
        source_doc_reference=body.source_doc_reference,
        tiers=tiers,
    )
    return success_envelope(PenaltyRuleResponse.model_validate(created), message="Penalty rule created.")


@router.get("/penalties/rules", response_model=Envelope[list[PenaltyRuleResponse]])
def list_penalty_rules(
    retailer_id: UUID | None = Query(default=None),
    rules: PenaltyRuleRepository = Depends(get_penalty_rule_repository),
) -> Envelope[list[PenaltyRuleResponse]]:
    rows = [PenaltyRuleResponse.model_validate(r) for r in rules.list_rules(retailer_id)]
    return success_envelope(rows)
