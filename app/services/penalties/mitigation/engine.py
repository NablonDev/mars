"""Ranks candidate mitigation actions against the ACCEPT baseline for a single open order.

Moved unchanged from `app/services/fine_mitigation/engine.py` (Phase 3 --
services move/folder-split); only the import paths below changed.

All cost/cause inputs here (ANTICIPATED_SHORTFALL_PCT, DELAY_PROBABILITY_TABLE) are
mocked, since no real Mars data source for them exists yet.
"""

from dataclasses import replace

from app.services.penalties.mitigation.types import MitigationInputs, MitigationOption, ShortageCause
from app.services.penalties.projection import (
    DELAY_VIOLATION_TYPES,
    SHORTAGE_VIOLATION_TYPES,
    OrderSnapshot,
    PenaltyRule,
    ProjectionEngine,
    ProjectionResult,
)
from app.services.penalties.projection.shortage import shortfall_units_for_pricing


class MitigationEngine:
    """Stateless entry point for the penalty-mitigation domain: ranks ACCEPT
    plus every structurally-eligible mitigation action for a single open
    order.

    Holds a `ProjectionEngine` instance so the hypothetical-scenario option
    generators below can re-run the projection engine without constructing
    a new one per option; the penalty rules and order snapshot themselves are
    per-call, not naturally constant, so they stay method arguments.
    """

    def __init__(self) -> None:
        self._projection_engine = ProjectionEngine()

    def evaluate(
        self,
        snapshot: OrderSnapshot,
        rules: list[PenaltyRule],
        projection: ProjectionResult,
        inputs: MitigationInputs,
    ) -> list[MitigationOption]:
        """Ranks ACCEPT plus every structurally-eligible mitigation action by net_saving, descending."""
        options = [self._accept_option(projection)]

        speed_up = self._speed_up_production_option(snapshot, rules, projection, inputs)
        if speed_up is not None:
            options.append(speed_up)

        split_shipment = self._split_shipment_option(snapshot, projection, inputs)
        if split_shipment is not None:
            options.append(split_shipment)

        faster_carrier = self._faster_carrier_option(snapshot, rules, projection, inputs)
        if faster_carrier is not None:
            options.append(faster_carrier)

        options.sort(key=lambda option: option.net_saving, reverse=True)
        return options

    def _accept_option(self, projection: ProjectionResult) -> MitigationOption:
        return MitigationOption(
            action="ACCEPT",
            projected_penalty_after=projection.total_expected_penalty_amount,
            action_cost=0.0,
            net_saving=0.0,
            risk_level="HIGH" if projection.total_expected_penalty_amount > 0 else "LOW",
            confidence="CONFIRMED",
            rationale="Pay the projected penalty as-is -- always knowable, no mitigation attempted.",
        )

    def _speed_up_production_option(
        self,
        snapshot: OrderSnapshot,
        rules: list[PenaltyRule],
        projection: ProjectionResult,
        inputs: MitigationInputs,
    ) -> MitigationOption | None:
        shortfall = shortfall_units_for_pricing(snapshot)
        if shortfall <= 0:
            return None
        if inputs.shortage_cause == ShortageCause.RAW_MATERIAL and inputs.shortage_cause_confirmed:
            return None  # known for certain that more labor/time won't help
        if inputs.capacity_boost_cost_per_unit is None or inputs.capacity_boost_max_units_per_day is None:
            return None  # "not present" tier -- nothing to compute from

        days_available = max((snapshot.required_ship_date - snapshot.projection_date).days, 0)
        closable_units = min(shortfall, inputs.capacity_boost_max_units_per_day * days_available)
        new_confirmed_qty = round(min(float(snapshot.order_qty), snapshot.confirmed_qty + closable_units))
        hypothetical = replace(snapshot, confirmed_qty=new_confirmed_qty)
        projected_penalty_after = self._projection_engine.project(
            hypothetical, rules, projection.stacking_mode
        ).total_expected_penalty_amount
        action_cost = closable_units * inputs.capacity_boost_cost_per_unit
        net_saving = projection.total_expected_penalty_amount - projected_penalty_after - action_cost

        confidence = (
            "CONFIRMED"
            if (
                inputs.shortage_cause_confirmed
                and inputs.shortage_cause == ShortageCause.LABOR_CAPACITY
                and inputs.capacity_boost_data_confirmed
            )
            else "ESTIMATED"
        )
        comfortable_margin = inputs.capacity_boost_max_units_per_day > 0 and days_available >= 2 * (
            shortfall / inputs.capacity_boost_max_units_per_day
        )
        risk_level = "LOW" if confidence == "CONFIRMED" and comfortable_margin else "MEDIUM"

        rationale = (
            f"Closes {closable_units:.0f} of {shortfall:.0f} short units over "
            f"{days_available} available day(s) at ${inputs.capacity_boost_cost_per_unit:.2f}/unit"
            f"{'' if confidence == 'CONFIRMED' else ' (shortage cause/cost data not fully confirmed)'}."
        )

        return MitigationOption(
            action="SPEED_UP_PRODUCTION",
            projected_penalty_after=round(projected_penalty_after, 2),
            action_cost=round(action_cost, 2),
            net_saving=round(net_saving, 2),
            risk_level=risk_level,
            confidence=confidence,
            rationale=rationale,
        )

    def _faster_carrier_option(
        self,
        snapshot: OrderSnapshot,
        rules: list[PenaltyRule],
        projection: ProjectionResult,
        inputs: MitigationInputs,
    ) -> MitigationOption | None:
        if not any(rule.violation_type in DELAY_VIOLATION_TYPES for rule in rules):
            return None  # no delay-type rule applies to this retailer
        if inputs.express_carrier_cost is None or inputs.express_carrier_transit_days is None:
            return None  # "not present" tier

        hypothetical = replace(snapshot, expected_transit_days=inputs.express_carrier_transit_days)
        projected_penalty_after = self._projection_engine.project(
            hypothetical, rules, projection.stacking_mode
        ).total_expected_penalty_amount
        action_cost = inputs.express_carrier_cost
        net_saving = projection.total_expected_penalty_amount - projected_penalty_after - action_cost

        confidence = "CONFIRMED" if inputs.express_carrier_data_confirmed else "ESTIMATED"
        risk_level = "LOW" if confidence == "CONFIRMED" else "MEDIUM"

        rationale = (
            f"Re-routes via an express carrier ({inputs.express_carrier_transit_days}-day transit) "
            f"for ${action_cost:.2f}"
            f"{'' if confidence == 'CONFIRMED' else ' (carrier cost/transit data not fully confirmed)'}."
        )

        return MitigationOption(
            action="FASTER_CARRIER",
            projected_penalty_after=round(projected_penalty_after, 2),
            action_cost=round(action_cost, 2),
            net_saving=round(net_saving, 2),
            risk_level=risk_level,
            confidence=confidence,
            rationale=rationale,
        )

    def _split_shipment_option(
        self, snapshot: OrderSnapshot, projection: ProjectionResult, inputs: MitigationInputs
    ) -> MitigationOption | None:
        if snapshot.confirmed_qty <= 0 or snapshot.confirmed_qty >= snapshot.order_qty:
            return None  # nothing ready to ship now, or nothing missing

        # The confirmed portion ships on the original date, so delay-type
        # violations don't apply to it -- but the engine already prices the
        # shortage penalty off the true confirmed_qty vs order_qty gap today, so
        # that half of `projection` needs no hypothetical re-run at all.
        shortage_only = [v for v in projection.violations if v.violation_type in SHORTAGE_VIOLATION_TYPES]
        if projection.stacking_mode == "MAX":
            projected_penalty_after = max((v.expected_penalty_amount for v in shortage_only), default=0.0)
        else:
            projected_penalty_after = sum(v.expected_penalty_amount for v in shortage_only)

        action_cost = inputs.split_shipment_handling_cost
        net_saving = projection.total_expected_penalty_amount - projected_penalty_after - action_cost

        rationale = (
            f"Ships the {snapshot.confirmed_qty} confirmed units on schedule and the remaining "
            f"{snapshot.order_qty - snapshot.confirmed_qty} units later -- avoids delay penalties, "
            "shortage penalties still apply."
        )

        return MitigationOption(
            action="SPLIT_SHIPMENT",
            projected_penalty_after=round(projected_penalty_after, 2),
            action_cost=round(action_cost, 2),
            net_saving=round(net_saving, 2),
            risk_level="MEDIUM",  # qualitative retailer-relationship risk, not computed
            confidence="CONFIRMED",  # reuses the already-trusted shortage pricing, no new assumption
            rationale=rationale,
        )
