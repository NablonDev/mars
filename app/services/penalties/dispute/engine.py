"""Deterministic dispute recompute-and-classify engine.

Reuses `app.services.penalties.projection.shortage.price_shortage_penalty`/
`app.services.penalties.projection.delay.price_delay_penalty` directly --
the same calc-type formulas (PER_UNIT/PERCENT_OF_PO/FLAT_FEE/TIERED) the
projection engine prices a *risk-adjusted* shortfall/delay with, fed here
with REAL, final post-delivery facts instead (see `DisputeFacts`'s module
docstring). This module never reimplements pricing logic -- only how much
of a rule's shortfall/delay measure actually happened, which the pure
pricing functions don't know how to compute themselves. Never calls
`app.services.penalties.projection.shortage.shortfall_units_for_pricing` --
that function's `ANTICIPATED_SHORTFALL_PCT` fallback is a pre-delivery risk
estimate, meaningless once delivery is final.

**LLM never decides pay/no-pay/how-much** -- this module (and this module
alone) does. `app.services.penalties.dispute.service.DisputeService.analyze`
is the only caller; the dispute-summary agent only narrates a verdict this
module already computed and persisted (see
`app.services.penalties.dispute.summary_service`'s module docstring).

**Known, documented gap inherited unchanged from the projection engine**:
`price_delay_penalty` raises `NotImplementedError` for a TIERED delay rule
(tiered pricing is implemented for shortage rules only, banded by
shortfall %, not for delay rules). `price_violation` catches that and
re-raises `UnsupportedDisputeCalcError`; `DisputeService.analyze` wraps
that into a clear `BusinessRuleError` rather than a raw `NotImplementedError`
reaching an API caller.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.services.penalties.dispute.types import (
    DisputeCalculation,
    DisputeFacts,
    DisputeVerdict,
    InsufficientDataForDisputeError,
    UnsupportedDisputeCalcError,
)
from app.services.penalties.projection.delay import price_delay_penalty
from app.services.penalties.projection.shortage import price_shortage_penalty
from app.services.penalties.projection.types import (
    DELAY_VIOLATION_TYPES,
    SHORTAGE_VIOLATION_TYPES,
    PenaltyRule,
)

#: Amounts within this many dollars of each other are treated as a match
#: (floating-point/rounding noise, not a genuine dispute) -- same 1-cent
#: tolerance convention as `round(..., 2)` used throughout this codebase's
#: money fields.
ROUNDING_TOLERANCE = 0.01


def compute_shortfall_units(facts: DisputeFacts) -> float:
    """Real, final shortfall -- unlike
    `app.services.penalties.projection.shortage.shortfall_units_for_pricing`,
    never falls back to a risk-adjusted estimate: a dispute adjudicates what
    actually was delivered, not what might have been. Caller must have
    already confirmed `facts.delivered_qty is not None`."""
    assert facts.delivered_qty is not None
    return max(0.0, facts.order_qty - facts.delivered_qty)


def compute_deadline(facts: DisputeFacts) -> date:
    return facts.required_delivery_date + timedelta(days=facts.grace_period_days)


def compute_is_late(facts: DisputeFacts) -> bool:
    """Caller must have already confirmed `facts.actual_delivery_date is
    not None`."""
    assert facts.actual_delivery_date is not None
    return facts.actual_delivery_date > compute_deadline(facts)


def price_violation(rule: PenaltyRule, facts: DisputeFacts) -> tuple[float, dict]:
    """Price one rule's violation against real, final post-delivery facts.

    Missing-fact guard runs first, before any math: a violation family
    whose required fact was never recorded as-of the charge date raises
    `InsufficientDataForDisputeError` -- never silently priced as if the
    fact were confirmed zero/on-time (see `DisputeFacts`'s docstring).

    Returns `(computed_amount, calc_trace)` -- `calc_trace` is a small,
    audit-trail dict (never machine-read back) capturing which family and
    measure this rule used; `DisputeService.analyze` folds it into
    `analysis_breakdown`.
    """
    if rule.violation_type in SHORTAGE_VIOLATION_TYPES:
        if facts.delivered_qty is None:
            raise InsufficientDataForDisputeError(
                f"No delivered quantity on record as-of the charge date for a "
                f"{rule.violation_type} dispute (rule {rule.rule_id})."
            )
        shortfall_units = compute_shortfall_units(facts)
        amount = price_shortage_penalty(rule, facts.order_qty, facts.unit_price, shortfall_units)
        return amount, {
            "violation_family": "SHORTAGE",
            "shortfall_units": shortfall_units,
        }

    if rule.violation_type in DELAY_VIOLATION_TYPES:
        if facts.actual_delivery_date is None:
            raise InsufficientDataForDisputeError(
                f"No actual delivery date on record as-of the charge date for a "
                f"{rule.violation_type} dispute (rule {rule.rule_id})."
            )
        deadline = compute_deadline(facts)
        is_late = facts.actual_delivery_date > deadline
        if not is_late:
            # Delivered within the grace-period window -- no real violation
            # occurred, regardless of what was charged.
            return 0.0, {
                "violation_family": "DELAY",
                "deadline": deadline,
                "is_late": False,
            }
        try:
            amount = price_delay_penalty(rule, facts.order_qty, facts.unit_price)
        except NotImplementedError as exc:
            raise UnsupportedDisputeCalcError(str(exc)) from exc
        return amount, {
            "violation_family": "DELAY",
            "deadline": deadline,
            "is_late": True,
        }

    raise ValueError(
        f"Rule {rule.rule_id} has violation_type={rule.violation_type!r}, not mapped to either "
        "SHORTAGE_VIOLATION_TYPES or DELAY_VIOLATION_TYPES."
    )


def classify(
    computed_amount: float, claimed_amount: float, tolerance: float = ROUNDING_TOLERANCE
) -> tuple[DisputeVerdict, float]:
    """Classify a dispute given the deterministically recomputed amount vs.
    what the retailer actually claimed. Returns `(verdict, delta_amount)`
    where `delta_amount = claimed_amount - computed_amount`.

    Branch order matters -- `computed_amount == 0` is checked before the
    tolerance check so a real violation that recomputes to exactly zero is
    always NO_PAY, not PAY_FULL, even in the degenerate case where
    `claimed_amount` also happens to be ~0.

    - `computed_amount == 0` (no real violation occurred, or it fell within
      the grace period): NO_PAY.
    - `abs(delta) <= tolerance`: the retailer's charge matches what Mars's
      own rule computes; PAY_FULL.
    - `delta > tolerance` (`computed_amount < claimed_amount`): the retailer
      overcharged relative to Mars's own rule; PAY_PARTIAL -- dispute the
      delta.
    - `delta < -tolerance` (`computed_amount > claimed_amount`): the
      retailer undercharged relative to Mars's own rule. PAY_FULL -- pay
      what was actually charged; the negative delta is recorded for audit
      only, never volunteered as a reason to pay more (locked decision).
    """
    delta = round(claimed_amount - computed_amount, 2)

    if computed_amount == 0:
        return DisputeVerdict.NO_PAY, delta
    if abs(delta) <= tolerance:
        return DisputeVerdict.PAY_FULL, delta
    if delta > tolerance:
        return DisputeVerdict.PAY_PARTIAL, delta
    return DisputeVerdict.PAY_FULL, delta


def recompute_dispute(rule: PenaltyRule, facts: DisputeFacts, claimed_amount: float) -> DisputeCalculation:
    """Full recompute-and-classify pass for one dispute."""
    computed_amount, calc_trace = price_violation(rule, facts)
    computed_amount = round(computed_amount, 2)
    verdict, delta_amount = classify(computed_amount, claimed_amount)

    # Approximation, documented: a rule's cap is treated as "applied" when
    # the final amount lands exactly on the cap. This can't distinguish a
    # penalty that happens to equal the cap unclipped from one that was
    # genuinely clipped -- reusing price_shortage_penalty/price_delay_penalty
    # as pure black boxes (see this module's docstring) means this engine
    # never sees the pre-cap amount to compare against.
    cap_amount = rule.cap_amount
    cap_applied = cap_amount is not None and computed_amount == round(cap_amount, 2)
    calc_trace = {**calc_trace, "cap_amount": cap_amount, "cap_applied": cap_applied}

    return DisputeCalculation(
        computed_amount=computed_amount,
        claimed_amount=round(claimed_amount, 2),
        delta_amount=delta_amount,
        verdict=verdict,
        calc_trace=calc_trace,
    )
