from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_fine_rule_repository
from app.repositories.fine_rule_repository import FineRuleRepository
from app.schemas.fine_rules import FineRuleRequest, FineRuleResponse

router = APIRouter(tags=["fine-rules"])


@router.post("/fine-rules", response_model=FineRuleResponse, status_code=201)
def create_fine_rule(
    body: FineRuleRequest, rules: FineRuleRepository = Depends(get_fine_rule_repository)
) -> dict:
    tiers = [t.model_dump() for t in body.tiers] if body.tiers else None
    rules.add_rule(
        rule_id=body.rule_id,
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
    return next(r for r in rules.list_rules(body.retailer_id) if r["rule_id"] == body.rule_id)


@router.get("/fine-rules", response_model=list[FineRuleResponse])
def list_fine_rules(
    retailer_id: str | None = Query(default=None),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
) -> list[dict]:
    return rules.list_rules(retailer_id)
