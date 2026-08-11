"""
The orchestrator -- this is the function a caller actually invokes. Wires
together the shortage and delay modules; pure, deterministic, no I/O.
"""

from app.engine.delay import compute_delay_probability, price_delay_fine
from app.engine.models import (
    DELAY_VIOLATION_TYPES,
    SHORTAGE_VIOLATION_TYPES,
    FineRule,
    OrderSnapshot,
    ProjectionResult,
    ViolationProjection,
)
from app.engine.shortage import compute_shortage_probability, price_shortage_fine, shortfall_units_for_pricing


def project_order(
    snapshot: OrderSnapshot, rules: list[FineRule], stacking_mode: str = "SUM"
) -> ProjectionResult:
    """
    Run the full projection for one order on one day.

    stacking_mode: "SUM" adds every violation's expected fine together
    (this is what every worked example in docs/FINE_ENGINE.md uses).
    "MAX" takes only the single largest expected fine, for retailers whose
    contracts are confirmed not to stack multiple violations on one order.
    Default is SUM to match the validated examples; switch per-retailer
    once the real stacking policy is confirmed (docs/FINE_ENGINE.md, "Open items").
    """
    shortage_prob = compute_shortage_probability(snapshot)
    delay_prob = compute_delay_probability(snapshot)
    shortfall_units = shortfall_units_for_pricing(snapshot)

    violations: list[ViolationProjection] = []
    for rule in rules:
        if rule.violation_type in SHORTAGE_VIOLATION_TYPES:
            probability = shortage_prob
            fine_if_realized = price_shortage_fine(
                rule, snapshot.order_qty, snapshot.unit_price, shortfall_units
            )
        elif rule.violation_type in DELAY_VIOLATION_TYPES:
            probability = delay_prob
            fine_if_realized = price_delay_fine(rule, snapshot.order_qty, snapshot.unit_price)
        else:
            raise ValueError(
                f"Rule {rule.rule_id} has violation_type '{rule.violation_type}' "
                "not mapped to either SHORTAGE_VIOLATION_TYPES or DELAY_VIOLATION_TYPES"
            )

        expected = probability * fine_if_realized
        violations.append(
            ViolationProjection(
                violation_type=rule.violation_type,
                rule_id=rule.rule_id,
                probability=round(probability, 4),
                fine_if_realized=round(fine_if_realized, 2),
                expected_fine=round(expected, 2),
            )
        )

    if stacking_mode == "MAX":
        total = max((v.expected_fine for v in violations), default=0.0)
    elif stacking_mode == "SUM":
        total = sum(v.expected_fine for v in violations)
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
        total_expected_fine=round(total, 2),
        stacking_mode=stacking_mode,
    )
