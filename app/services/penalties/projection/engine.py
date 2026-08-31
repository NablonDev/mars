"""Orchestrates shortage and delay calculations into an order projection.

Moved unchanged from `app/services/fine_projection/engine.py` (Phase 3 --
services move/folder-split); only the import paths below changed.
"""

from app.services.penalties.projection.delay import compute_delay_probability, price_delay_penalty
from app.services.penalties.projection.shortage import (
    compute_shortage_probability,
    price_shortage_penalty,
    shortfall_units_for_pricing,
)
from app.services.penalties.projection.types import (
    DELAY_VIOLATION_TYPES,
    SHORTAGE_VIOLATION_TYPES,
    OrderSnapshot,
    PenaltyRule,
    ProjectionResult,
    ViolationProjection,
)


class ProjectionEngine:
    """Stateless entry point for the penalty-projection domain: combines the
    shortage and delay calculation models into one order-level projection.

    No constructor state -- each call is fresh with its own snapshot, rule
    set, and stacking mode, so there is nothing naturally constant to hold
    across invocations.
    """

    def project(
        self, snapshot: OrderSnapshot, rules: list[PenaltyRule], stacking_mode: str = "SUM"
    ) -> ProjectionResult:
        """Projects shortage and delay penalties using the specified stacking mode."""
        shortage_prob = compute_shortage_probability(snapshot)
        delay_prob = compute_delay_probability(snapshot)
        shortfall_units = shortfall_units_for_pricing(snapshot)

        violations: list[ViolationProjection] = []

        for rule in rules:
            if rule.violation_type in SHORTAGE_VIOLATION_TYPES:
                probability = shortage_prob
                penalty_if_realized = price_shortage_penalty(
                    rule, snapshot.order_qty, snapshot.unit_price, shortfall_units
                )
            elif rule.violation_type in DELAY_VIOLATION_TYPES:
                probability = delay_prob
                penalty_if_realized = price_delay_penalty(rule, snapshot.order_qty, snapshot.unit_price)
            else:
                raise ValueError(
                    f"Rule {rule.rule_id} has violation_type '{rule.violation_type}' "
                    "not mapped to either SHORTAGE_VIOLATION_TYPES or DELAY_VIOLATION_TYPES"
                )

            expected = probability * penalty_if_realized
            violations.append(
                ViolationProjection(
                    violation_type=rule.violation_type,
                    rule_id=rule.rule_id,
                    probability=round(probability, 4),
                    penalty_if_realized=round(penalty_if_realized, 2),
                    expected_penalty=round(expected, 2),
                )
            )

        if stacking_mode == "MAX":
            total = max((v.expected_penalty for v in violations), default=0.0)
        elif stacking_mode == "SUM":
            total = sum(v.expected_penalty for v in violations)
        else:
            raise ValueError("stacking_mode must be 'SUM' or 'MAX'")

        days_to_delivery = (snapshot.requested_delivery_date - snapshot.projection_date).days

        return ProjectionResult(
            order_id=snapshot.order_id,
            projection_date=snapshot.projection_date,
            days_to_delivery=days_to_delivery,
            shortage_probability=round(shortage_prob, 4),
            delay_probability=round(delay_prob, 4),
            violations=violations,
            total_expected_penalty=round(total, 2),
            stacking_mode=stacking_mode,
        )
