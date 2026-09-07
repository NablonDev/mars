"""Enums, errors, and data structures for the penalty-dispute engine.

Framework-free, same posture as `app.services.penalties.projection.types`:
no SQLAlchemy/FastAPI imports here or in `engine.py` (this package reuses
`app.services.penalties.projection.shortage`/`.delay`'s pricing functions
directly rather than reimplementing them -- see `engine.py`'s docstring).
Note `app.core.exceptions` itself imports FastAPI (its handler registration
lives in the same module as the exception classes), so the two error types
below are plain `ValueError` subclasses, not `app.core.exceptions.
BusinessRuleError` -- `app.services.penalties.dispute.service.DisputeService.
analyze` is the only place these get translated into the real
`BusinessRuleError(code=...)` an API caller sees.

`DisputeFacts` is deliberately NOT `app.services.penalties.projection.types.
OrderSnapshot`: the projection engine's snapshot mixes real facts with
probability-driving inputs (production_status, appointment_status, carrier
reliability, ...) that a post-delivery dispute has no use for -- a dispute
adjudicates what actually happened, not what might happen. `DisputeFacts`
carries only the real, final facts the pricing functions need, plus
`grace_period_days` (not a field on
`app.services.penalties.projection.types.PenaltyRule` -- that dataclass's
contract is explicitly frozen; see its module docstring).

`delivered_qty` comes from actual delivery (`common.delivery`/
`delivery_line`), never from `order_confirmation` -- that table is the
retailer's pre-delivery promise, not what actually shipped. A dispute
adjudicates money after the fact, so it must use the real, final quantity
(see `app.repositories.common.fulfillment.
get_delivered_quantity_for_purchase_order_not_after`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class DisputeVerdict(Enum):
    NO_PAY = "NO_PAY"
    PAY_PARTIAL = "PAY_PARTIAL"
    PAY_FULL = "PAY_FULL"


class InsufficientDataForDisputeError(ValueError):
    """Raised by `engine.price_violation`, before any pricing math, when a
    fact this violation's family needs was never recorded as-of the
    historical charge date (`delivered_qty` for SHORTAGE,
    `actual_delivery_date` for DELAY). Missing must never collapse into
    "confirmed zero"/"confirmed on time" -- that would let the engine
    wrongly refuse (or wrongly grant) a charge based on absence of data.
    `DisputeService.analyze` catches this and re-raises
    `BusinessRuleError(code="INSUFFICIENT_DATA_FOR_DISPUTE")`, leaving the
    dispute untouched (still OPEN)."""


class UnsupportedDisputeCalcError(ValueError):
    """Raised by `engine.price_violation` when the effective rule's
    `calc_type` cannot be priced for this violation family at all --
    today, only a TIERED delay rule (`price_delay_penalty` has no tiered
    branch; tiered pricing exists for shortage rules only, banded by
    shortfall %, not for delay rules banded by days-late). `DisputeService.
    analyze` catches this and re-raises
    `BusinessRuleError(code="DISPUTE_CALC_NOT_SUPPORTED")`, leaving the
    dispute untouched (still OPEN)."""


@dataclass
class DisputeFacts:
    """Real, final post-delivery facts as of the historical charge date --
    never the probability-driven/risk-adjusted estimates
    `app.services.penalties.projection` uses."""

    order_qty: int
    unit_price: float
    # Real, final delivered quantity as of the charge date -- None means no
    # delivery fact is on record at all as of that date (never "confirmed
    # zero"; see InsufficientDataForDisputeError). SHORTAGE-family disputes
    # only.
    delivered_qty: float | None
    required_delivery_date: date
    # None if no delivery/shipment fact is on record yet. DELAY-family
    # disputes only.
    actual_delivery_date: date | None
    grace_period_days: int = 0


@dataclass
class DisputeCalculation:
    """Everything `PenaltyDispute.analyzed_at` onward gets persisted from."""

    computed_amount: float
    claimed_amount: float
    delta_amount: float  # claimed_amount - computed_amount
    verdict: DisputeVerdict
    # Audit trail feeding `analysis_breakdown` -- violation family, the
    # shortfall/lateness measure actually used, cap info. Never read back
    # by the engine itself.
    calc_trace: dict
