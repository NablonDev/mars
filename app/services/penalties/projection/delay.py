"""Delay probability and pricing calculations."""

from datetime import date, timedelta

from app.services.penalties.projection.types import (
    AppointmentStatus,
    CalcType,
    OrderSnapshot,
    PenaltyRule,
    ProductionStatus,
)

# (bucket key computed from buffer_days) x (stage 1-4) -> base probability
DELAY_PROBABILITY_TABLE = {
    "ge_0": {1: 0.05, 2: 0.05, 3: 0.05, 4: 0.02},
    "eq_-1": {1: 0.15, 2: 0.30, 3: 0.50, 4: 0.85},
    "eq_-2": {1: 0.30, 2: 0.50, 3: 0.70, 4: 0.95},
    "le_-3": {1: 0.50, 2: 0.70, 3: 0.88, 4: 0.98},
}

# Production risk is a leading indicator for ship-date slip, not just for
# shortage. Deliberately modest, and last in resolve_expected_ship_date()'s
# priority order: a real appointment reschedule, an actual ship date, or an
# explicit caller estimate all carry better information than a generic
# status-based guess. Mirrors ANTICIPATED_SHORTFALL_PCT on the shortage side.
PRODUCTION_STATUS_SHIP_SLIP_DAYS = {
    ProductionStatus.ON_TRACK: 0,
    ProductionStatus.AT_RISK: 1,
    ProductionStatus.BEHIND: 2,
}


def _buffer_bucket(buffer_days: int) -> str:
    """Map slack days onto a `DELAY_PROBABILITY_TABLE` row key.

    `buffer_days` is requested delivery minus expected delivery, so negative
    means late. Three or more days over collapse into one ceiling bucket;
    marginal risk beyond that point isn't modeled.
    """
    if buffer_days >= 0:
        return "ge_0"
    if buffer_days == -1:
        return "eq_-1"
    if buffer_days == -2:
        return "eq_-2"
    return "le_-3"


def _carrier_multiplier(reliability_score: float) -> float:
    """Scale the base delay probability up for a less reliable carrier.

    Multiplies `compute_delay_probability`'s table lookup, so a poor carrier
    amplifies buffer- and stage-driven risk rather than replacing it.
    """
    if reliability_score >= 90:
        return 1.0
    if reliability_score >= 75:
        return 1.2
    if reliability_score >= 60:
        return 1.5
    return 2.0


def resolve_expected_ship_date(s: OrderSnapshot) -> date:
    """Best current estimate of the ship date.

    Sources are checked most-to-least trustworthy: actual date, caller-supplied
    estimate, missed appointment, then a generic production-status guess.
    """
    if s.actual_ship_date is not None:
        return s.actual_ship_date
    if s.expected_ship_date is not None:
        return s.expected_ship_date
    if s.appointment_status == AppointmentStatus.MISSED:
        return s.required_ship_date + timedelta(days=1)
    slip = PRODUCTION_STATUS_SHIP_SLIP_DAYS[s.production_status]
    return s.required_ship_date + timedelta(days=slip)


def compute_stage(s: OrderSnapshot) -> int:
    """Classify how close the order is to shipping, as a 1-4 column key into `DELAY_PROBABILITY_TABLE`.

    Stage 4 short-circuits once `actual_ship_date` is set: a confirmed buffer
    predicts better than any estimate. Later stages carry higher base
    probabilities for the same buffer, reflecting less time left to recover.
    """
    if s.actual_ship_date is not None:
        return 4
    days_before_ship = (s.required_ship_date - s.projection_date).days
    if days_before_ship > 4:
        return 1
    if days_before_ship >= 2:
        return 2
    return 3


def compute_delay_probability(s: OrderSnapshot) -> float:
    """Estimate the probability this order misses its requested delivery date.

    Capped at 0.98 rather than treated as a certainty: a shipment already in
    transit can still arrive early.
    """
    expected_ship = resolve_expected_ship_date(s)
    expected_delivery = expected_ship + timedelta(days=s.expected_transit_days)
    buffer_days = (s.requested_delivery_date - expected_delivery).days

    stage = compute_stage(s)
    base = DELAY_PROBABILITY_TABLE[_buffer_bucket(buffer_days)][stage]
    prob = base * _carrier_multiplier(s.carrier_reliability_score)
    return min(0.98, prob)


def price_delay_penalty(rule: PenaltyRule, order_qty: int, unit_price: float) -> float:
    """Flat reference cost for the violation type, not scaled by the current buffer."""
    if rule.calc_type == CalcType.PERCENT_OF_PO:
        penalty = rule.rate * order_qty * unit_price
    elif rule.calc_type == CalcType.FLAT_FEE:
        penalty = rule.rate
    elif rule.calc_type == CalcType.PER_UNIT:
        penalty = rule.rate * order_qty
    else:
        # Includes CalcType.TIERED: tiered pricing is implemented for
        # shortage rules (banded by gap_pct) but not yet for delay rules
        # (which would need to be banded by days-late instead). This is a
        # known, documented gap, not a silent failure mode.
        raise NotImplementedError(f"calc_type={rule.calc_type} not supported for delay rule {rule.rule_id}")

    if rule.cap_amount is not None:
        penalty = min(penalty, rule.cap_amount)
    return penalty
