"""Enums, errors, and data structures for the penalty-dispute engine.

Framework-free, matching `app.services.penalties.projection.types`: no
SQLAlchemy or FastAPI imports here or in `engine.py`. `app.core.exceptions`
registers its FastAPI handlers in the same module as its exception classes, so
the two error types below subclass plain `ValueError` instead;
`DisputeResolutionService.analyze` is the only place they become the
`BusinessRuleError(code=...)` an API caller sees.

`DisputeFacts` is deliberately not `OrderSnapshot`. The projection snapshot
mixes real facts with probability-driving inputs (production status,
appointment status, carrier reliability) that a post-delivery dispute has no
use for. `DisputeFacts` carries only the final facts the pricing functions
need, plus `grace_period_days`, which `PenaltyRule`'s frozen contract excludes.

`delivered_qty` comes from actual delivery (`common.delivery`,
`delivery_line`), never from `order_confirmation`: that table holds the
retailer's pre-delivery promise, not what actually shipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class DisputeVerdict(Enum):
    """Outcome of comparing the claimed penalty amount against the computed one."""

    NO_PAY = "NO_PAY"
    PAY_PARTIAL = "PAY_PARTIAL"
    PAY_FULL = "PAY_FULL"


class InsufficientDataForDisputeError(ValueError):
    """A fact the violation family needs was never recorded as of the charge date.

    That is `delivered_qty` for SHORTAGE, `actual_delivery_date` for DELAY.
    Missing must never collapse into "confirmed zero" or "confirmed on time",
    which would let the engine refuse or grant a charge on absence of data.
    `DisputeResolutionService.analyze` re-raises this as
    `BusinessRuleError(code="INSUFFICIENT_DATA_FOR_DISPUTE")`, leaving the
    dispute OPEN.
    """


class UnsupportedDisputeCalcError(ValueError):
    """The effective rule's `calc_type` cannot be priced for this violation family.

    Today that means only a TIERED delay rule: tiered pricing exists for
    shortage rules alone, banded by shortfall percentage rather than days late.
    `DisputeResolutionService.analyze` re-raises this as
    `BusinessRuleError(code="DISPUTE_CALC_NOT_SUPPORTED")`, leaving the dispute
    OPEN.
    """


@dataclass
class DisputeFacts:
    """Real, final post-delivery facts as of the historical charge date.

    Never the probability-driven estimates `app.services.penalties.projection`
    works from.
    """

    order_qty: int
    unit_price: float
    # Real, final delivered quantity as of the charge date. None means no
    # delivery fact is on record at that date, never "confirmed zero" (see
    # InsufficientDataForDisputeError). SHORTAGE-family disputes only.
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
    # Audit trail feeding `analysis_breakdown`: violation family, the shortfall
    # or lateness measure actually used, cap info. Never read back by the
    # engine itself.
    calc_trace: dict
