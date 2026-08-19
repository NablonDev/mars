"""Repository for fine rules, including TIERED bands (dim_fine_rule_tier)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidFineRuleDataError
from app.models import FineRule as FineRuleModel
from app.models import FineRuleTier
from app.services.fine_projection import CalcType, FineRule, FineTier

_CALC_TYPE_MAP = {
    "PER_UNIT": CalcType.PER_UNIT,
    "PERCENT_OF_PO": CalcType.PERCENT_OF_PO,
    "FLAT_FEE": CalcType.FLAT_FEE,
    "TIERED": CalcType.TIERED,
}


def _rule_to_dict(r: FineRuleModel) -> dict:
    return {
        "rule_id": r.rule_id,
        "retailer_id": r.retailer_id,
        "violation_type": r.violation_type,
        "calc_type": r.calc_type,
        "rate": float(r.rate),
        "threshold_pct": float(r.threshold_pct or 0.0),
        "cap_amount": float(r.cap_amount) if r.cap_amount is not None else None,
        "is_active": r.is_active,
        "grace_period_days": r.grace_period_days,
        "effective_start_date": r.effective_start_date,
        "effective_end_date": r.effective_end_date,
        "source_doc_reference": r.source_doc_reference,
    }


class FineRuleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_rule(
        self,
        rule_id: str,
        retailer_id: str,
        violation_type: str,
        calc_type: str,
        rate: float,
        threshold_pct: float = 0.0,
        cap_amount: float | None = None,
        grace_period_days: int = 0,
        effective_start_date: date | None = None,
        effective_end_date: date | None = None,
        source_doc_reference: str | None = None,
        tiers: list[dict] | None = None,
    ) -> dict:
        rule = FineRuleModel(
            rule_id=rule_id,
            retailer_id=retailer_id,
            violation_type=violation_type,
            calc_type=calc_type,
            rate=rate,
            threshold_pct=threshold_pct,
            cap_amount=cap_amount,
            grace_period_days=grace_period_days,
            effective_start_date=effective_start_date or date(2026, 1, 1),
            effective_end_date=effective_end_date,
            source_doc_reference=source_doc_reference,
        )
        self._session.add(rule)
        self._session.flush()

        for i, tier in enumerate(tiers or []):
            self._session.add(
                FineRuleTier(
                    tier_id=f"{rule_id}-TIER-{i:02d}",
                    rule_id=rule_id,
                    band_min=tier["band_min"],
                    band_max=tier["band_max"],
                    rate=tier["rate"],
                )
            )

        self._session.flush()
        return _rule_to_dict(rule)

    def list_rules_for_retailer(self, retailer_id: str) -> list[FineRule]:
        rows = self._session.scalars(
            select(FineRuleModel).where(
                FineRuleModel.retailer_id == retailer_id,
                FineRuleModel.is_active.is_(True),
            )
        ).all()

        rules: list[FineRule] = []

        for r in rows:
            calc_type = _CALC_TYPE_MAP.get(r.calc_type)
            if calc_type is None:
                raise InvalidFineRuleDataError(
                    f"Rule {r.rule_id!r} has calc_type={r.calc_type!r}, which is not one of "
                    f"{sorted(_CALC_TYPE_MAP)}. Fix the row in dim_fine_rule."
                )

            tiers = None
            if calc_type == CalcType.TIERED:
                tier_rows = self._session.scalars(
                    select(FineRuleTier).where(FineRuleTier.rule_id == r.rule_id)
                ).all()

                tiers = [
                    FineTier(band_min=float(t.band_min), band_max=float(t.band_max), rate=float(t.rate))
                    for t in tier_rows
                ]

            rules.append(
                FineRule(
                    rule_id=r.rule_id,
                    violation_type=r.violation_type,
                    calc_type=calc_type,
                    rate=float(r.rate),
                    threshold_pct=float(r.threshold_pct or 0.0),
                    cap_amount=float(r.cap_amount) if r.cap_amount is not None else None,
                    tiers=tiers,
                )
            )
        return rules

    def get_rules_for_retailer(self, retailer_id: str) -> list[FineRule]:
        """Deprecated alias for list_rules_for_retailer -- kept only because
        app/services/fine_summary.py is off-limits to edit in this pass."""
        return self.list_rules_for_retailer(retailer_id)

    def list_rules(self, retailer_id: str | None = None) -> list[dict]:
        stmt = select(FineRuleModel)
        if retailer_id:
            stmt = stmt.where(FineRuleModel.retailer_id == retailer_id)

        rows = self._session.scalars(stmt).all()
        return [_rule_to_dict(r) for r in rows]
