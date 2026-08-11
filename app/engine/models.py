"""
Enums and input/output data structures for the fine engine. Pure
dataclasses/enums only -- no I/O, no SQLAlchemy, no FastAPI.
"""

from dataclasses import dataclass
from datetime import date
from enum import Enum


class ProductionStatus(Enum):
    ON_TRACK = "ON_TRACK"
    AT_RISK = "AT_RISK"
    BEHIND = "BEHIND"


class AppointmentStatus(Enum):
    SCHEDULED = "SCHEDULED"
    RESCHEDULED = "RESCHEDULED"
    MISSED = "MISSED"
    COMPLETED = "COMPLETED"


class CalcType(Enum):
    PER_UNIT = "PER_UNIT"
    PERCENT_OF_PO = "PERCENT_OF_PO"
    FLAT_FEE = "FLAT_FEE"
    TIERED = "TIERED"


@dataclass
class FineTier:
    """One band of a tiered rule: if the measure (gap_pct for shortage
    rules) falls in [band_min, band_max), this rate applies. Bands should
    be contiguous and non-overlapping -- not validated here, validate at
    rule-authoring time."""

    band_min: float
    band_max: float
    rate: float


# Which violation types are priced off the shortage model vs. the delay
# model. Extend this if a retailer introduces a new violation category.
SHORTAGE_VIOLATION_TYPES = {"SHORT_SHIP", "FILL_RATE"}
DELAY_VIOLATION_TYPES = {"OTIF_LATE", "ASN_LATE"}
# ASN_LATE is mapped onto the delay probability model as an APPROXIMATION:
# late paperwork correlates with late shipments but is not the same signal,
# and this engine has no dedicated ASN-submission-timing input yet. Treat
# any ASN_LATE projection as directionally useful, not calibrated on its
# own -- see docs/FINE_ENGINE.md "Open items".


@dataclass
class FineRule:
    rule_id: str
    violation_type: str  # e.g. "SHORT_SHIP", "OTIF_LATE", "FILL_RATE"
    calc_type: CalcType
    rate: float = 0.0  # meaning depends on calc_type; unused when tiers is set
    threshold_pct: float = 0.0  # FRACTION, e.g. 0.02 for 2% -- never a whole-number percent.
    # See docs/FINE_ENGINE.md "Open items" for why this distinction
    # is called out explicitly: a schema that stored 2.0 here instead
    # of 0.02 would silently produce a 100x pricing error.
    cap_amount: float | None = None
    tiers: list[FineTier] | None = None  # required when calc_type == TIERED

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold_pct <= 1.0:
            raise ValueError(
                f"Rule {self.rule_id}: threshold_pct={self.threshold_pct!r} is out of range. "
                "This field is a fraction (0.02 for 2%), not a whole-number percent (2.0). "
                "A value outside [0, 1] almost always means the wrong unit was loaded."
            )
        if self.calc_type == CalcType.TIERED and not self.tiers:
            raise ValueError(f"Rule {self.rule_id}: calc_type=TIERED requires tiers to be set")


@dataclass
class OrderSnapshot:
    """Everything about one order as of one projection_date. This is the
    only input the engine needs -- assembling it from SAP/Datalliance/
    portal data is the caller's job, not the engine's."""

    order_id: str
    projection_date: date
    order_qty: int
    unit_price: float
    requested_delivery_date: date
    required_ship_date: date

    confirmed_qty: int  # latest SAP ATP / Cut Order Report figure
    production_status: ProductionStatus
    demand_exception_flagged: bool = False  # early warning, before any confirmed cut

    expected_ship_date: date | None = None  # explicit override, e.g. a real rescheduled
    # DC appointment date. None -> assume on pace
    # unless appointment_status says otherwise.
    actual_ship_date: date | None = None  # set once physically departed
    appointment_status: AppointmentStatus = AppointmentStatus.SCHEDULED
    carrier_reliability_score: float = 90.0
    expected_transit_days: int = 2


@dataclass
class ViolationProjection:
    violation_type: str
    rule_id: str
    probability: float
    fine_if_realized: float
    expected_fine: float


@dataclass
class ProjectionResult:
    order_id: str
    projection_date: date
    days_to_delivery: int
    shortage_probability: float
    delay_probability: float
    violations: list[ViolationProjection]
    total_expected_fine: float
    stacking_mode: str
