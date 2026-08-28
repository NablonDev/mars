"""fine_projection-exclusive seed data (fine rules, worked-example orders)
and the day-by-day scenario replay that exercises FineProjectionService."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, TypedDict

from app.core.exceptions import OrderNotFoundError
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.order import OrderRepository
from app.services.fine_projection import DELAY_VIOLATION_TYPES, SHORTAGE_VIOLATION_TYPES, FineRule
from app.services.fine_projection.po_delivery_change import PoDeliveryChangeRequestService
from app.services.fine_projection.service import FineProjectionService
from app.services.seeding.scenario_data_projection import (
    AMZ_RULES,
    WMT_RULES,
    amz1_days,
    amz2_days,
    wmt2_days,
    wmt_days,
)

# Source-doc references aren't part of the pure FineRule dataclass
# (deliberately -- that dataclass stays minimal/pure), so they're kept
# here as the one piece of seed-only metadata layered on top of it.
_SOURCE_DOC_REFERENCE = {
    "RULE-WMT-SHORT": "Walmart Supplier Manual v2026.1 (mock)",
    "RULE-WMT-OTIF": "Walmart Supplier Manual v2026.1 (mock)",
    "RULE-AMZ-FILL": "Amazon Vendor Central Chargeback Policy (mock)",
    "RULE-AMZ-OTIF": "Amazon Vendor Central Chargeback Policy (mock)",
}


def _rule_to_seed_dict(rule: FineRule, retailer_id: str) -> dict[str, Any]:
    """Converts a canonical WMT_RULES/AMZ_RULES FineRule (the same objects
    the pure-engine tests use) into FineRuleRepository.add_rule kwargs, so
    seed data and validated test numbers can't drift apart."""
    return {
        "rule_id": rule.rule_id,
        "retailer_id": retailer_id,
        "violation_type": rule.violation_type,
        "calc_type": rule.calc_type.value,
        "rate": rule.rate,
        "threshold_pct": rule.threshold_pct,
        "cap_amount": rule.cap_amount,
        "source_doc_reference": _SOURCE_DOC_REFERENCE.get(rule.rule_id),
    }


_RULES = [_rule_to_seed_dict(r, "RET-WMT") for r in WMT_RULES] + [
    _rule_to_seed_dict(r, "RET-AMZ") for r in AMZ_RULES
]


def _day_before_first(days) -> date:
    """order_date convention in every worked example: one day before the
    first tracked projection day (SAP order placed the day before the
    first daily snapshot we have facts for)."""
    return days[0][0].projection_date - timedelta(days=1)


# Earliest date across all four scenarios in scenario_data_projection.py
# (WMT-100234's order_date, one day before its first tracked day). `seed()`
# shifts every literal Aug-2026 date in `_ORDERS` by `calendar_offset()` so
# a fresh seed run re-anchors the whole fixed calendar around real "today"
# instead of drifting into the past as wall-clock time passes it -- see
# CLAUDE.local.md/PROGRESS.local.md for the incident this fixes.
CALENDAR_BASE_DATE = date(2026, 8, 1)


def calendar_offset() -> timedelta:
    """The offset `seed()` applies to every `_ORDERS` date. Exposed so
    tests exercising the real seed()/simulate_daily_run() path can convert
    their literal Aug-2026 assertions without duplicating this formula.

    `simulate_daily_run()` does NOT call this directly -- it derives its
    per-order offset from the order's already-persisted `order_date`
    instead (see its docstring), so that a seed() and a later
    simulate_daily_run() call landing on different real days still agree
    on the offset actually baked into that order."""
    return date.today() - CALENDAR_BASE_DATE  # noqa: DTZ011 -- wall-clock anchor by design, see module docstring


class _OrderSeed(TypedDict):
    order_id: str
    retailer_id: str
    sku_id: str
    ship_from_location_id: str
    order_qty: int
    unit_price: float
    order_date: date
    requested_delivery_date: date
    required_ship_date: date
    order_status: str
    carrier_id: str


_ORDERS: list[_OrderSeed] = [
    {
        "order_id": "WMT-100234",
        "retailer_id": "RET-WMT",
        "sku_id": "SKU-PED30",
        "ship_from_location_id": "LOC-ATL",
        "order_qty": 2000,
        "unit_price": 18.0,
        "order_date": _day_before_first(wmt_days),
        "requested_delivery_date": wmt_days[0][0].requested_delivery_date,
        "required_ship_date": wmt_days[0][0].required_ship_date,
        "order_status": "OPEN",
        "carrier_id": "CAR-SWIFT",
    },
    {
        "order_id": "WMT-100511",
        "retailer_id": "RET-WMT",
        "sku_id": "SKU-CES12",
        "ship_from_location_id": "LOC-ATL",
        "order_qty": 1500,
        "unit_price": 18.0,
        "order_date": _day_before_first(wmt2_days),
        "requested_delivery_date": wmt2_days[0][0].requested_delivery_date,
        "required_ship_date": wmt2_days[0][0].required_ship_date,
        "order_status": "OPEN",
        "carrier_id": "CAR-SWIFT",
    },
    {
        "order_id": "AMZ-778501",
        "retailer_id": "RET-AMZ",
        "sku_id": "SKU-WHI20",
        "ship_from_location_id": "LOC-COL",
        "order_qty": 1200,
        "unit_price": 14.0,
        "order_date": _day_before_first(amz1_days),
        "requested_delivery_date": amz1_days[0][0].requested_delivery_date,
        "required_ship_date": amz1_days[0][0].required_ship_date,
        "order_status": "OPEN",
        "carrier_id": "CAR-JBHUNT",
    },
    {
        "order_id": "AMZ-780112",
        "retailer_id": "RET-AMZ",
        "sku_id": "SKU-WHI20",
        "ship_from_location_id": "LOC-COL",
        "order_qty": 900,
        "unit_price": 14.0,
        "order_date": _day_before_first(amz2_days),
        "requested_delivery_date": amz2_days[0][0].requested_delivery_date,
        "required_ship_date": amz2_days[0][0].required_ship_date,
        "order_status": "OPEN",
        "carrier_id": "CAR-JBHUNT",
    },
]
_SCENARIOS = [
    ("WMT-100234", wmt_days),
    ("WMT-100511", wmt2_days),
    ("AMZ-778501", amz1_days),
    ("AMZ-780112", amz2_days),
]

# Original (unshifted) order_date per order, exactly what `_ORDERS` was
# built from -- `simulate_daily_run()` diffs this against the order's
# actually-persisted order_date to recover the per-order offset `seed()`
# applied, without calling date.today() a second time.
_ORIGINAL_ORDER_DATE: dict[str, date] = {order_id: _day_before_first(days) for order_id, days in _SCENARIOS}


class _NegotiationCreateSpec(TypedDict):
    """When (day) and how a PO delivery-change request is fired during that
    order's day-by-day replay -- `trigger_date` matches a `projection_date`
    already present in that order's own scenario days above."""

    trigger_date: date
    reason_code: str
    proposed_delivery_date: date
    notes: str | None
    now: datetime


class _NegotiationRespondSpec(TypedDict):
    """The retailer's response, fired the day after `create`."""

    trigger_date: date
    decision: str
    countered_delivery_date: date | None
    now: datetime


class _NegotiationScenario(TypedDict, total=False):
    create: _NegotiationCreateSpec
    respond: _NegotiationRespondSpec
    # Fired once the order's day-loop has finished, instead of on a
    # specific day -- the AMZ-780112 request is deliberately never
    # responded to, so it must be swept for a timeout instead.
    expire_as_of: datetime


# One outcome per order, demonstrating all four PO delivery-change-request
# retailer-response paths against the four existing worked-example
# scenarios/orders -- see docs/redesign-schema.md and
# app/services/fine_projection/po_delivery_change.py's module docstring for
# the feature itself. Every `now`/`as_of` is anchored inside this scenario's
# fictional Aug-2026 calendar (never wall-clock) so timestamps stay
# chronologically correct relative to the day-by-day replay.
_NEGOTIATION_SCENARIOS: dict[str, _NegotiationScenario] = {
    # ACCEPTED: SAP confirms the real production cut (1,850/2,000) on Aug 5;
    # Walmart accepts a 3-day extension the next day. current_delivery_date
    # shifts Aug 11 -> Aug 14, current_required_ship_date Aug 9 -> Aug 12 by
    # the same delta -- so the Aug 9 appointment-miss day, once the day-loop
    # reaches it, is re-projected against the new required ship date instead
    # of the original one.
    "WMT-100234": {
        "create": {
            "trigger_date": date(2026, 8, 5),
            "reason_code": "SHORTAGE",
            "proposed_delivery_date": date(2026, 8, 14),
            "notes": "SAP confirms a real cut to 1,850/2,000; requesting 3 extra days.",
            "now": datetime(2026, 8, 5, 9, 0),  # noqa: DTZ001 -- naive by design, matches PoDeliveryChangeRequestService._utcnow()
        },
        "respond": {
            "trigger_date": date(2026, 8, 6),
            "decision": "ACCEPTED",
            "countered_delivery_date": None,
            "now": datetime(2026, 8, 6, 9, 0),  # noqa: DTZ001 -- naive by design, matches PoDeliveryChangeRequestService._utcnow()
        },
    },
    # COUNTERED: DC reschedules the dock appointment a day later on Aug 8;
    # Walmart counters the requested Aug 19 with Aug 17 the next day
    # (strictly between baseline Aug 15 and proposed Aug 19).
    "WMT-100511": {
        "create": {
            "trigger_date": date(2026, 8, 8),
            "reason_code": "DELAY",
            "proposed_delivery_date": date(2026, 8, 19),
            "notes": "Dock appointment rescheduled a day later; requesting buffer.",
            "now": datetime(2026, 8, 8, 9, 0),  # noqa: DTZ001 -- naive by design, matches PoDeliveryChangeRequestService._utcnow()
        },
        "respond": {
            "trigger_date": date(2026, 8, 9),
            "decision": "COUNTERED",
            "countered_delivery_date": date(2026, 8, 17),
            "now": datetime(2026, 8, 9, 9, 0),  # noqa: DTZ001 -- naive by design, matches PoDeliveryChangeRequestService._utcnow()
        },
    },
    # REJECTED: the confirmed cut worsens to 1,080/1,200 and production
    # escalates to BEHIND on Aug 7, with no recovery for the rest of the
    # scenario; Amazon rejects the next day. No shadow tracking -- the daily
    # projection continues against the original (unchanged) date.
    "AMZ-778501": {
        "create": {
            "trigger_date": date(2026, 8, 7),
            "reason_code": "SHORTAGE",
            "proposed_delivery_date": date(2026, 8, 18),
            "notes": "Confirmed cut worsens to 1,080/1,200; production status BEHIND.",
            "now": datetime(2026, 8, 7, 9, 0),  # noqa: DTZ001 -- naive by design, matches PoDeliveryChangeRequestService._utcnow()
        },
        "respond": {
            "trigger_date": date(2026, 8, 8),
            "decision": "REJECTED",
            "countered_delivery_date": None,
            "now": datetime(2026, 8, 8, 9, 0),  # noqa: DTZ001 -- naive by design, matches PoDeliveryChangeRequestService._utcnow()
        },
    },
    # EXPIRED/timeout: a QA hold is flagged on Aug 14, exactly 2 days before
    # the Aug 16 required ship date -- right at the min_lead_days=2 boundary.
    # reason_code=OTHER (a QA/quality hold is neither a quantity shortage
    # nor a carrier/transit delay). Never responded to; Amazon's 24h SLA
    # means it expires at Aug 15 09:00, well before the scenario's last day
    # (Aug 17), so the recovery sweep is exercised at the end of the
    # day-loop instead of a `respond` entry.
    "AMZ-780112": {
        "create": {
            "trigger_date": date(2026, 8, 14),
            "reason_code": "OTHER",
            "proposed_delivery_date": date(2026, 8, 21),
            "notes": "QA hold on finished batch; requesting buffer in case release slips",
            "now": datetime(2026, 8, 14, 9, 0),  # noqa: DTZ001 -- naive by design, matches PoDeliveryChangeRequestService._utcnow()
        },
        "expire_as_of": datetime(2026, 8, 17, 9, 0),  # noqa: DTZ001 -- naive by design, matches PoDeliveryChangeRequestService._utcnow()
    },
}


def seed(rules: FineRuleRepository, orders: OrderRepository) -> dict[str, int]:
    """Idempotent: safe to call repeatedly. Skips anything that already
    exists rather than erroring on a duplicate key."""
    counts = {"rules": 0, "orders": 0}

    existing_rules = {r["rule_id"] for r in rules.list_rules()}
    for rule_dict in _RULES:
        if rule_dict["rule_id"] not in existing_rules:
            rules.add_rule(**rule_dict)
            counts["rules"] += 1

    offset = calendar_offset()
    for o in _ORDERS:
        if orders.get_order(o["order_id"]) is None:
            orders.create_order(
                **{
                    **o,
                    "order_date": o["order_date"] + offset,
                    "requested_delivery_date": o["requested_delivery_date"] + offset,
                    "required_ship_date": o["required_ship_date"] + offset,
                }
            )
            counts["orders"] += 1

    return counts


def simulate_daily_run(
    orders: OrderRepository,
    projection_service: FineProjectionService,
    po_delivery_change_service: PoDeliveryChangeRequestService,
) -> list[dict]:
    """Walks all four scenarios day by day: writes each day's facts, runs
    the projection, and marks the order DELIVERED after its final day.

    Interleaved into each order's day-loop, at the calendar days fixed by
    `_NEGOTIATION_SCENARIOS`, is exactly one PO delivery-change-request
    negotiation outcome per order -- ACCEPTED/COUNTERED/REJECTED/EXPIRED,
    one of each, so all four retailer-response paths are demonstrated end
    to end against real day-by-day scenario replay. Each accept/counter/
    reject/expire call re-triggers `FineProjectionService.run_for_order`
    internally (see `PoDeliveryChangeRequestService`), so no extra
    projection call is made here -- the next day's own iteration already
    reflects the outcome.
    """
    summaries = []
    production_seq = 0
    for order_id, days in _SCENARIOS:
        order = orders.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id, hint="Call seed_master_data() first.")

        # Derived from what's actually persisted, not a fresh date.today()
        # call -- seed() and this call can land on different real days, and
        # this must recover exactly the offset seed() baked into this order
        # for the two to agree (see calendar_offset()'s docstring).
        offset = order["order_date"] - _ORIGINAL_ORDER_DATE[order_id]

        negotiation_scenario = _NEGOTIATION_SCENARIOS.get(order_id)
        negotiation_request_id: str | None = None
        negotiation_result: dict[str, Any] | None = None
        # Idempotent, same convention as seed(): a re-run must not
        # double-create a request or error retrying a terminal one (e.g.
        # WMT-100511's baseline_delivery_date has already moved by the time
        # of a second call, so replaying the same hardcoded
        # countered_delivery_date against it would raise
        # InvalidPoDeliveryChangeResponseError). If this order already has
        # any negotiation history, skip re-firing and surface the prior
        # outcome instead.
        existing_negotiation_history = (
            po_delivery_change_service.list_history(order_id) if negotiation_scenario is not None else []
        )
        if existing_negotiation_history:
            negotiation_scenario = None
            negotiation_result = existing_negotiation_history[-1]

        daily_results = []
        for snapshot, note in days:
            # Every date that flows from scenario_data_projection.py into a
            # repository write call (or the projection call itself) is
            # shifted here, at the point of use -- the negotiation
            # trigger-date comparisons below deliberately keep comparing the
            # original, unshifted snapshot.projection_date instead.
            shifted_date = snapshot.projection_date + offset
            orders.add_confirmation(
                order_id=order_id,
                confirmation_id=f"CONF-{order_id}-{shifted_date.isoformat()}",
                confirmed_qty=snapshot.confirmed_qty,
                confirmation_date=datetime.combine(shifted_date, datetime.min.time()),
            )
            production_seq += 1
            orders.add_production_status(
                production_id=f"PROD-{order['sku_id']}-{order['ship_from_location_id']}-"
                f"{shifted_date.isoformat()}-{production_seq:04d}",
                sku_id=order["sku_id"],
                location_id=order["ship_from_location_id"],
                status=snapshot.production_status.value,
                status_date=datetime.combine(shifted_date, datetime.min.time()),
            )
            if snapshot.demand_exception_flagged:
                orders.add_demand_exception(
                    exception_id=f"EXC-{order_id}-{shifted_date.isoformat()}",
                    order_id=order_id,
                    flagged_date=shifted_date,
                )
            orders.record_shipment_event(
                order_id=order_id,
                carrier_id=order["carrier_id"],
                expected_ship_date=(
                    snapshot.expected_ship_date + offset if snapshot.expected_ship_date else None
                ),
                actual_ship_date=(snapshot.actual_ship_date + offset if snapshot.actual_ship_date else None),
                appointment_status=snapshot.appointment_status.value,
                expected_transit_days=snapshot.expected_transit_days,
                recorded_at=datetime.combine(shifted_date, datetime.min.time()),
            )

            result = projection_service.run_for_order(order_id, shifted_date)
            # Per-model dollar breakdown, not just the combined total --
            # summed across whichever violations happen to be shortage-
            # vs. delay-priced for this retailer's rule set (usually one
            # of each, but this doesn't assume that -- a retailer with
            # two shortage-type rules active would sum both correctly).
            shortage_fine = sum(
                v.expected_fine for v in result.violations if v.violation_type in SHORTAGE_VIOLATION_TYPES
            )
            delay_fine = sum(
                v.expected_fine for v in result.violations if v.violation_type in DELAY_VIOLATION_TYPES
            )
            daily_results.append(
                {
                    "projection_date": result.projection_date,
                    "note": note,
                    "shortage_fine": round(shortage_fine, 2),
                    "delay_fine": round(delay_fine, 2),
                    "total_expected_fine": result.total_expected_fine,
                    "shortage_probability": result.shortage_probability,
                    "delay_probability": result.delay_probability,
                }
            )

            if negotiation_scenario is not None:
                create_spec = negotiation_scenario.get("create")
                if (
                    create_spec is not None
                    and negotiation_request_id is None
                    and snapshot.projection_date == create_spec["trigger_date"]
                ):
                    created = po_delivery_change_service.create_request(
                        order_id=order_id,
                        reason_code=create_spec["reason_code"],
                        proposed_delivery_date=create_spec["proposed_delivery_date"] + offset,
                        notes=create_spec["notes"],
                        now=create_spec["now"] + offset,
                    )
                    negotiation_request_id = created["request_id"]

                respond_spec = negotiation_scenario.get("respond")
                if (
                    respond_spec is not None
                    and negotiation_request_id is not None
                    and negotiation_result is None
                    and snapshot.projection_date == respond_spec["trigger_date"]
                ):
                    negotiation_result = po_delivery_change_service.record_response(
                        request_id=negotiation_request_id,
                        decision=respond_spec["decision"],
                        countered_delivery_date=(
                            respond_spec["countered_delivery_date"] + offset
                            if respond_spec["countered_delivery_date"] is not None
                            else None
                        ),
                        now=respond_spec["now"] + offset,
                    )

        expire_as_of = None if negotiation_scenario is None else negotiation_scenario.get("expire_as_of")
        if expire_as_of is not None:
            expired = po_delivery_change_service.expire_stale(as_of=expire_as_of + offset)
            negotiation_result = next(
                (row for row in expired if row["request_id"] == negotiation_request_id), None
            )

        orders.set_order_status(order_id, "DELIVERED")
        summary: dict[str, Any] = {"order_id": order_id, "days": daily_results}
        if negotiation_result is not None:
            summary["negotiation"] = negotiation_result
        summaries.append(summary)

    return summaries
