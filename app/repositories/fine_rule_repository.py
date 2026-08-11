"""
Repository for fine rules, including TIERED bands.

Fixes carried over from the FastAPI/Alembic refactor round (see
docs/FINE_ENGINE.md changelog): TIERED rules load their bands from
dim_fine_rule_tier into FineTier objects, and an unrecognized
calc_type raises a clear ValueError naming the rule -- instead of a bare
KeyError from a dict lookup.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine import CalcType, FineRule, FineTier
from app.models import FineRuleORM, FineRuleTier

_CALC_TYPE_MAP = {
    "PER_UNIT": CalcType.PER_UNIT,
    "PERCENT_OF_PO": CalcType.PERCENT_OF_PO,
    "FLAT_FEE": CalcType.FLAT_FEE,
    "TIERED": CalcType.TIERED,
}


class FineRuleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_rule(
        self,
        rule_id,
        retailer_id,
        violation_type,
        calc_type,
        rate,
        threshold_pct=0.0,
        cap_amount=None,
        grace_period_days=0,
        effective_start_date=None,
        effective_end_date=None,
        source_doc_reference=None,
        tiers=None,
    ) -> None:
        self._session.add(
            FineRuleORM(
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
        )
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

    def get_rules_for_retailer(self, retailer_id: str) -> list[FineRule]:
        rows = self._session.scalars(
            select(FineRuleORM).where(
                FineRuleORM.retailer_id == retailer_id,
                FineRuleORM.is_active.is_(True),
            )
        ).all()

        rules: list[FineRule] = []
        for r in rows:
            calc_type = _CALC_TYPE_MAP.get(r.calc_type)
            if calc_type is None:
                raise ValueError(
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

    def list_rules(self, retailer_id: str | None = None) -> list[dict]:
        stmt = select(FineRuleORM)
        if retailer_id:
            stmt = stmt.where(FineRuleORM.retailer_id == retailer_id)
        rows = self._session.scalars(stmt).all()
        return [
            {
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
            for r in rows
        ]
