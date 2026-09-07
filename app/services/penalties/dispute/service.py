"""Orchestrates the dispute lifecycle: open, analyze (deterministic verdict
compute-and-persist), resolve/override, and read.

No LangGraph, no job-queue for `analyze()` -- synchronous, per the locked
design decision (a human never blocks on approval before a verdict is
written; they resolve/override it afterward via a plain API call). Mirrors
`app.services.penalties.delivery_change.PoDeliveryChangeRequestService`'s
shape (a dataclass of injected repositories/services, one method per
lifecycle transition).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.models.enums import DisputeStatus
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.dispute import PenaltyDisputeRepository
from app.repositories.penalties.projection import ActualPenaltyRepository
from app.repositories.penalties.rule import PenaltyRuleRepository
from app.services.penalties.dispute.engine import recompute_dispute
from app.services.penalties.dispute.types import (
    DisputeFacts,
    InsufficientDataForDisputeError,
    UnsupportedDisputeCalcError,
)
from app.services.penalties.projection.service import ProjectionService
from app.services.penalties.projection.types import CalcType
from app.services.penalties.projection.types import PenaltyRule as PenaltyRuleValue
from app.utils.clock import utc_now

_TERMINAL_STATUSES = {DisputeStatus.RESOLVED, DisputeStatus.OVERRIDDEN}


def _to_rule_value(rule_row: dict, tiers) -> PenaltyRuleValue:
    """Convert a `PenaltyRuleRepository.list_rules_effective_on` dict row
    into the pure-engine `PenaltyRuleValue` dataclass `recompute_dispute`
    needs -- same conversion `PenaltyRuleRepository.list_rules_for_retailer`
    does inline for the projection engine, duplicated narrowly here since
    that method returns dataclasses already and carries no
    `grace_period_days`/dict escape hatch (see
    `app.services.penalties.dispute.types`'s module docstring)."""
    return PenaltyRuleValue(
        rule_id=str(rule_row["id"]),
        violation_type=rule_row["violation_type"],
        calc_type=CalcType(rule_row["calc_type"]),
        rate=rule_row["rate"],
        threshold_pct=rule_row["threshold_pct"],
        cap_amount=rule_row["cap_amount"],
        tiers=tiers or None,
    )


@dataclass
class DisputeService:
    purchase_orders: PurchaseOrderRepository
    disputes: PenaltyDisputeRepository
    actual_penalties: ActualPenaltyRepository
    rules: PenaltyRuleRepository
    projection_service: ProjectionService

    def open_dispute(
        self,
        actual_penalty_id: UUID,
        reason_code: str,
        claimed_amount: float,
        notes: str | None = None,
    ) -> dict:
        """Opens a new dispute against an already-recorded
        `actual_penalty` charge. Enforces "at most one OPEN/ANALYZED
        dispute per charge" -- a retailer can amend a charge, producing a
        second dispute cycle once the first is terminal (RESOLVED/
        OVERRIDDEN); see `PenaltyDisputeRepository.find_active_for_actual_
        penalty`'s docstring."""
        actual_penalty = self.actual_penalties.get(actual_penalty_id)
        if actual_penalty is None:
            raise NotFoundError(
                code="ACTUAL_PENALTY_NOT_FOUND",
                message=f"No actual penalty found with actual_penalty_id={actual_penalty_id}",
            )

        active = self.disputes.find_active_for_actual_penalty(actual_penalty_id)
        if active is not None:
            raise ConflictError(
                code="ACTIVE_DISPUTE_EXISTS",
                message=(
                    f"Actual penalty {actual_penalty_id} already has an active "
                    f"(OPEN/ANALYZED) dispute ({active['id']})."
                ),
            )

        purchase_order_id = actual_penalty["purchase_order_id"]
        self.purchase_orders.require_purchase_order(purchase_order_id)

        return self.disputes.create(
            actual_penalty_id=actual_penalty_id,
            purchase_order_id=purchase_order_id,
            reason_code=reason_code,
            claimed_amount=claimed_amount,
            notes=notes,
        )

    def analyze(self, dispute_id: UUID, now: datetime | None = None) -> dict:
        """Loads real, final post-delivery facts as-of the historical
        charge date, resolves the effective rule, runs the deterministic
        engine, and persists the verdict -- moves the dispute to ANALYZED.

        Raises, leaving the dispute completely untouched (still OPEN/
        whatever it was, no partial write):
        - `BusinessRuleError(code="NO_MATCHING_RULE_FOR_DISPUTE")` -- no
          rule was effective for the charge's `(retailer, violation_type)`
          on the charge date.
        - `BusinessRuleError(code="INSUFFICIENT_DATA_FOR_DISPUTE")` -- the
          fact this violation family needs (delivered_qty for SHORTAGE,
          actual_delivery_date for DELAY) was never recorded as-of the
          charge date.
        - `BusinessRuleError(code="DISPUTE_CALC_NOT_SUPPORTED")` -- the
          effective rule's calc_type cannot be priced for this violation
          family (a TIERED delay rule today).

        Re-analyzable while still OPEN or already ANALYZED (e.g. a rule
        record was corrected after the first pass); refuses once RESOLVED/
        OVERRIDDEN -- a human decision has already been recorded on top of
        a verdict, and silently recomputing underneath it would strand that
        decision against a different verdict.
        """
        dispute = self._require_dispute(dispute_id)
        if dispute["dispute_status"] in _TERMINAL_STATUSES:
            raise ValidationError(
                code="DISPUTE_ALREADY_RESOLVED",
                message=(
                    f"Dispute {dispute_id} is already {dispute['dispute_status']} and cannot be re-analyzed."
                ),
            )

        actual_penalty = self.actual_penalties.get(dispute["actual_penalty_id"])
        if actual_penalty is None:
            raise NotFoundError(
                code="ACTUAL_PENALTY_NOT_FOUND",
                message=f"No actual penalty found with actual_penalty_id={dispute['actual_penalty_id']}",
            )
        purchase_order = self.purchase_orders.require_purchase_order(dispute["purchase_order_id"])
        as_of_date = actual_penalty["invoice_or_deduction_date"]

        candidates = self.rules.list_rules_effective_on(purchase_order["retailer_id"], as_of_date)
        matching = [r for r in candidates if r["violation_type"] == actual_penalty["violation_type"]]
        if not matching:
            raise BusinessRuleError(
                code="NO_MATCHING_RULE_FOR_DISPUTE",
                message=(
                    f"No penalty rule was effective for retailer {purchase_order['retailer_id']}, "
                    f"violation_type={actual_penalty['violation_type']!r} on {as_of_date.isoformat()} "
                    f"(dispute {dispute_id})."
                ),
            )
        # Deterministic tie-break when more than one rule matched (should not
        # normally happen -- effective date ranges for the same retailer/
        # violation_type are expected not to overlap): the most recently
        # effective rule wins.
        rule_row = max(matching, key=lambda r: r["effective_start_date"])

        tiers = (
            self.rules.get_tiers_for_rule(rule_row["id"])
            if rule_row["calc_type"] == CalcType.TIERED.value
            else []
        )
        rule_value = _to_rule_value(rule_row, tiers)

        fulfillment = self.projection_service.fulfillment
        # order_qty/unit_price/required_delivery_date come from the same
        # snapshot-assembly logic the projection engine uses (including its
        # current_delivery_date-falls-back-to-requested_delivery_date
        # precedence) -- safe here because a dispute always runs after
        # delivery is final. snapshot.confirmed_qty is deliberately unused:
        # it comes from order_confirmation (the pre-delivery promise), not
        # the real, final delivered quantity a dispute must use (see
        # DisputeFacts's module docstring).
        snapshot = self.projection_service.build_snapshot(dispute["purchase_order_id"], as_of_date)
        delivered_qty = fulfillment.get_delivered_quantity_for_purchase_order_not_after(
            dispute["purchase_order_id"], as_of_date
        )
        shipment = fulfillment.get_latest_shipment_for_purchase_order_not_after(
            dispute["purchase_order_id"], as_of_date
        )
        actual_delivery_date = shipment["actual_delivery_date"] if shipment is not None else None

        facts = DisputeFacts(
            order_qty=snapshot.order_qty,
            unit_price=snapshot.unit_price,
            delivered_qty=delivered_qty,
            required_delivery_date=snapshot.requested_delivery_date,
            actual_delivery_date=actual_delivery_date,
            grace_period_days=rule_row["grace_period_days"],
        )

        try:
            calculation = recompute_dispute(rule_value, facts, dispute["claimed_amount"])
        except InsufficientDataForDisputeError as exc:
            raise BusinessRuleError(
                code="INSUFFICIENT_DATA_FOR_DISPUTE",
                message=f"Cannot adjudicate dispute {dispute_id}: {exc}",
            ) from exc
        except UnsupportedDisputeCalcError as exc:
            raise BusinessRuleError(
                code="DISPUTE_CALC_NOT_SUPPORTED",
                message=f"Cannot adjudicate dispute {dispute_id}: {exc}",
            ) from exc

        calc_trace = calculation.calc_trace
        deadline = calc_trace.get("deadline")
        breakdown = {
            "rule_id": str(rule_row["id"]),
            "rule_code": rule_row["rule_code"],
            "calc_type": rule_row["calc_type"],
            "violation_family": calc_trace.get("violation_family"),
            "as_of_date": as_of_date.isoformat(),
            "facts": {
                "order_qty": facts.order_qty,
                "unit_price": facts.unit_price,
                "delivered_qty": facts.delivered_qty,
                "shortfall_units": calc_trace.get("shortfall_units"),
                "required_delivery_date": facts.required_delivery_date.isoformat(),
                "actual_delivery_date": (
                    facts.actual_delivery_date.isoformat() if facts.actual_delivery_date is not None else None
                ),
                "deadline": deadline.isoformat() if deadline is not None else None,
                "is_late": calc_trace.get("is_late"),
                "grace_period_days": facts.grace_period_days,
            },
            "cap_amount": calc_trace.get("cap_amount"),
            "cap_applied": calc_trace.get("cap_applied"),
            "claimed_amount": calculation.claimed_amount,
            "computed_amount": calculation.computed_amount,
            "delta_amount": calculation.delta_amount,
        }

        return self.disputes.save_verdict(
            dispute_id,
            rule_id=rule_row["id"],
            computed_amount=calculation.computed_amount,
            delta_amount=calculation.delta_amount,
            verdict=calculation.verdict.value,
            analysis_breakdown=breakdown,
            analyzed_at=now or utc_now(),
        )

    def resolve(
        self,
        dispute_id: UUID,
        resolved_by: str,
        override_verdict: str | None = None,
        override_reason: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Requires ANALYZED status. `override_verdict` set -> OVERRIDDEN
        (requires `override_reason`); otherwise -> RESOLVED (accepts the
        engine's own verdict as-is)."""
        dispute = self._require_dispute(dispute_id)
        if dispute["dispute_status"] != DisputeStatus.ANALYZED:
            raise ValidationError(
                code="DISPUTE_NOT_ANALYZED",
                message=(
                    f"Dispute {dispute_id} is not ANALYZED (status={dispute['dispute_status']!r}); "
                    "run analyze() first."
                ),
            )
        if override_verdict is not None and not override_reason:
            raise ValidationError(
                code="OVERRIDE_REASON_REQUIRED",
                message="override_reason is required when override_verdict is set.",
            )

        status = DisputeStatus.OVERRIDDEN if override_verdict is not None else DisputeStatus.RESOLVED
        return self.disputes.resolve(
            dispute_id,
            dispute_status=status,
            resolved_by=resolved_by,
            resolved_at=now or utc_now(),
            override_verdict=override_verdict,
            override_reason=override_reason,
        )

    def get(self, dispute_id: UUID) -> dict:
        return self._require_dispute(dispute_id)

    def list_for_purchase_order(self, purchase_order_id: UUID | None = None) -> list[dict]:
        return self.disputes.list_for_purchase_order(purchase_order_id)

    def _require_dispute(self, dispute_id: UUID) -> dict:
        dispute = self.disputes.get_by_id(dispute_id)
        if dispute is None:
            raise NotFoundError(
                code="DISPUTE_NOT_FOUND", message=f"No penalty dispute found with dispute_id={dispute_id}"
            )
        return dispute
