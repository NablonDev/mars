"""Calculates shortage probability and shortage-related penalties.

Moved unchanged from `app/services/fine_projection/shortage.py` (Phase 3 --
services move/folder-split); only the import path below changed.
"""

from app.services.penalties.projection.types import CalcType, OrderSnapshot, PenaltyRule, ProductionStatus

# Estimated shortfall used when no confirmed cut exists.
ANTICIPATED_SHORTFALL_PCT = {
    ProductionStatus.ON_TRACK: 0.00,
    ProductionStatus.AT_RISK: 0.10,
    ProductionStatus.BEHIND: 0.25,
}

STATUS_POINTS = {
    ProductionStatus.ON_TRACK: 0,
    ProductionStatus.AT_RISK: 15,
    ProductionStatus.BEHIND: 30,
}

# Near-certain probability once a physical shipment confirms a shortfall.
SHORTAGE_LOCKED_IN_PROBABILITY = 0.95


def _gap_points(gap_pct: float) -> int:
    if gap_pct <= 0:
        return 0
    if gap_pct < 0.10:
        return 10
    if gap_pct <= 0.30:
        return 25
    return 40


def _days_points(days_to_delivery: int) -> int:
    if days_to_delivery >= 8:
        return 0
    if days_to_delivery >= 4:
        return 10
    if days_to_delivery >= 1:
        return 20
    return 30


def _score_to_probability(score: int) -> float:
    bands = [(10, 0.05), (25, 0.15), (45, 0.35), (65, 0.55), (85, 0.75)]
    for upper, prob in bands:
        if score <= upper:
            return prob
    return 0.92


def _demand_exception_points(s: OrderSnapshot) -> int:
    # Count the exception only before a confirmed cut or status escalation.
    if (
        s.demand_exception_flagged
        and s.production_status == ProductionStatus.ON_TRACK
        and s.confirmed_qty >= s.order_qty
    ):
        return 5
    return 0


def compute_shortage_probability(s: OrderSnapshot) -> float:
    actual_shortfall = s.order_qty - s.confirmed_qty
    if s.actual_ship_date is not None and actual_shortfall > 0:
        return SHORTAGE_LOCKED_IN_PROBABILITY

    gap_pct = actual_shortfall / s.order_qty if s.order_qty else 0.0
    days_to_delivery = (s.requested_delivery_date - s.projection_date).days
    score = (
        _gap_points(gap_pct)
        + STATUS_POINTS[s.production_status]
        + _days_points(days_to_delivery)
        + _demand_exception_points(s)
    )
    return _score_to_probability(score)


def shortfall_units_for_pricing(s: OrderSnapshot) -> float:
    """Real confirmed shortfall if one exists, otherwise a risk-adjusted
    estimate driven by production status. Real data always wins."""
    actual_shortfall = s.order_qty - s.confirmed_qty
    if actual_shortfall > 0:
        return float(actual_shortfall)
    return ANTICIPATED_SHORTFALL_PCT[s.production_status] * s.order_qty


def _price_tiered(rule: PenaltyRule, measure: float, po_value: float) -> float:
    if not rule.tiers:
        return 0.0
    for tier in rule.tiers:
        if tier.band_min <= measure < tier.band_max:
            return tier.rate * po_value
    return 0.0  # measure fell above every defined band -- no matching tier


def price_shortage_penalty(
    rule: PenaltyRule, order_qty: int, unit_price: float, shortfall_units: float
) -> float:
    threshold_units = rule.threshold_pct * order_qty
    penalized_units = max(0.0, shortfall_units - threshold_units)
    if penalized_units <= 0:
        return 0.0

    if rule.calc_type == CalcType.PER_UNIT:
        penalty = penalized_units * rule.rate
    elif rule.calc_type == CalcType.PERCENT_OF_PO:
        # Flat once breached; does not scale with shortfall size.
        penalty = rule.rate * order_qty * unit_price
    elif rule.calc_type == CalcType.FLAT_FEE:
        penalty = rule.rate
    elif rule.calc_type == CalcType.TIERED:
        # Tiered shortage penalties are based on shortfall percentage of PO value.
        gap_pct = shortfall_units / order_qty if order_qty else 0.0
        penalty = _price_tiered(rule, gap_pct, order_qty * unit_price)
    else:
        raise NotImplementedError(f"Unsupported calc_type for rule {rule.rule_id}")

    if rule.cap_amount is not None:
        penalty = min(penalty, rule.cap_amount)
    return penalty
