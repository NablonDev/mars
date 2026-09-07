"""Repository for `penalties.penalty_rule` and its optional
`penalty_rule_tier` bands. Was `app/repositories/fine_rule.py`
(`FineRuleRepository`, full `fine`/`fines` -> `penalty`/`penalties` rename).

`PenaltyRule`/`PenaltyRuleTier` gain a UUID surrogate `id`; `rule_code` is
now the natural business key (was `rule_id`), and `PenaltyRuleTier.rule_id`
FKs to `PenaltyRule.id`, not the business key -- tier rows are looked up
via that FK, not `rule_code`, directly.

**Naming collision, same pattern as `app/repositories/penalties/mitigation.py`'s
`MitigationOption`:** the ORM models `app.models.penalties.rule.PenaltyRule`/
`PenaltyRuleTier` and the pure-engine dataclasses
`app.services.penalties.projection.types.PenaltyRule`/`PenaltyRuleTier`
share the same class names -- both are imported below, aliased to keep
them apart.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models import PenaltyRule as PenaltyRuleModel
from app.models import PenaltyRuleTier as PenaltyRuleTierModel
from app.services.penalties.projection import CalcType
from app.services.penalties.projection import PenaltyRule as PenaltyRuleValue
from app.services.penalties.projection import PenaltyRuleTier as PenaltyRuleTierValue

_CALC_TYPE_MAP = {
    "PER_UNIT": CalcType.PER_UNIT,
    "PERCENT_OF_PO": CalcType.PERCENT_OF_PO,
    "FLAT_FEE": CalcType.FLAT_FEE,
    "TIERED": CalcType.TIERED,
}


def _rule_to_dict(r: PenaltyRuleModel) -> dict:
    return {
        "id": r.id,
        "rule_code": r.rule_code,
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


class PenaltyRuleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_rule(
        self,
        rule_code: str,
        retailer_id: UUID,
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
        rule = PenaltyRuleModel(
            rule_code=rule_code,
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
                PenaltyRuleTierModel(
                    rule_id=rule.id,
                    tier_code=f"TIER-{i:02d}",
                    band_min=tier["band_min"],
                    band_max=tier["band_max"],
                    rate=tier["rate"],
                )
            )

        self._session.flush()
        return _rule_to_dict(rule)

    def list_rules_for_retailer(self, retailer_id: UUID) -> list[PenaltyRuleValue]:
        rows = self._session.scalars(
            select(PenaltyRuleModel).where(
                PenaltyRuleModel.retailer_id == retailer_id,
                PenaltyRuleModel.is_active.is_(True),
            )
        ).all()

        rules: list[PenaltyRuleValue] = []

        for r in rows:
            calc_type = _CALC_TYPE_MAP.get(r.calc_type)
            if calc_type is None:
                raise ValidationError(
                    code="INVALID_PENALTY_RULE_DATA",
                    message=(
                        f"Rule {r.rule_code!r} has calc_type={r.calc_type!r}, which is not one of "
                        f"{sorted(_CALC_TYPE_MAP)}. Fix the row in penalty_rule."
                    ),
                )

            tiers = None
            if calc_type == CalcType.TIERED:
                tier_rows = self._session.scalars(
                    select(PenaltyRuleTierModel).where(PenaltyRuleTierModel.rule_id == r.id)
                ).all()

                tiers = [
                    PenaltyRuleTierValue(
                        band_min=float(t.band_min), band_max=float(t.band_max), rate=float(t.rate)
                    )
                    for t in tier_rows
                ]

            rules.append(
                PenaltyRuleValue(
                    # Deliberate Phase 3 fix (flagged in the phase report): `rule_id`
                    # must be the surrogate `penalty_rule.id`, not `rule_code` --
                    # `PenaltyProjectionRepository.save_result` writes this value
                    # straight into `penalty_projection.rule_id`, a UUID FK to
                    # `penalty_rule.id`. `rule_code` (e.g. "RULE-WMT-SHORT") stays
                    # available via `list_rules()`/`list_rules_for_retailer` dict
                    # rows for anything that needs the human-legible business key.
                    rule_id=str(r.id),
                    violation_type=r.violation_type,
                    calc_type=calc_type,
                    rate=float(r.rate),
                    threshold_pct=float(r.threshold_pct or 0.0),
                    cap_amount=float(r.cap_amount) if r.cap_amount is not None else None,
                    tiers=tiers,
                )
            )
        return rules

    def list_rules_effective_on(self, retailer_id: UUID, as_of_date: date) -> list[dict]:
        """Rules for a retailer that were actually in force on a specific
        historical date -- unlike `list_rules_for_retailer` (used by
        projection/mitigation, which prices *today's* orders against
        *currently* active rules and ignores `effective_start_date`/
        `effective_end_date` entirely), adjudicating a historical charge
        needs the rule that was effective on the charge date, which may
        since have been superseded or deactivated.

        Returns plain dicts (not the pure-engine `PenaltyRuleValue`
        dataclass `list_rules_for_retailer` returns) -- the dispute engine
        needs `grace_period_days` alongside the calc fields, which has no
        home on that dataclass (see `app.services.penalties.dispute.types`'
        module docstring); `app.services.penalties.dispute.service`
        converts the calc fields into a `PenaltyRuleValue` itself, tier
        rows included, at the one call site that needs it.

        `is_active` is NOT filtered here (unlike `list_rules_for_retailer`)
        -- a rule later deactivated is still the one that was effective on
        a past charge date; only the date-range columns are checked.
        """
        rows = self._session.scalars(
            select(PenaltyRuleModel).where(
                PenaltyRuleModel.retailer_id == retailer_id,
                PenaltyRuleModel.effective_start_date <= as_of_date,
                (PenaltyRuleModel.effective_end_date.is_(None))
                | (PenaltyRuleModel.effective_end_date >= as_of_date),
            )
        ).all()
        return [_rule_to_dict(r) for r in rows]

    def get_tiers_for_rule(self, rule_id: UUID) -> list[PenaltyRuleTierValue]:
        """Pure-engine tier bands for one rule id -- factored out of
        `list_rules_for_retailer`'s inline tier-loading loop so
        `list_rules_effective_on` callers (dict rows, no dataclass) can
        reuse the same tier lookup without duplicating it."""
        tier_rows = self._session.scalars(
            select(PenaltyRuleTierModel).where(PenaltyRuleTierModel.rule_id == rule_id)
        ).all()
        return [
            PenaltyRuleTierValue(band_min=float(t.band_min), band_max=float(t.band_max), rate=float(t.rate))
            for t in tier_rows
        ]

    def list_rules(self, retailer_id: UUID | None = None) -> list[dict]:
        stmt = select(PenaltyRuleModel)
        if retailer_id:
            stmt = stmt.where(PenaltyRuleModel.retailer_id == retailer_id)

        rows = self._session.scalars(stmt).all()
        return [_rule_to_dict(r) for r in rows]

    def truncate_all(self) -> None:
        """Deletes every penalty_rule row and its penalty_rule_tier
        children, for a force-reseed. Caller must first clear
        penalty_projection (it FKs to penalty_rule)."""
        self._session.execute(delete(PenaltyRuleTierModel))
        self._session.execute(delete(PenaltyRuleModel))
        self._session.flush()
