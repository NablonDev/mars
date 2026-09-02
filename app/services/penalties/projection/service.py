"""Orchestrates one penalty-projection run: assemble the snapshot, load the
retailer's rules, run the pure engine, persist, return the result.

Was `app/services/fine_projection/service.py`'s `FineProjectionService`
(renamed `ProjectionService`, folder-split per the approved plan). Rewritten
against the Phase 2 `common`/`penalties` repositories -- `OrderRepository`/
`FineRuleRepository`/`MasterDataRepository`/`ProjectionRepository` no longer
exist; see `app/repositories/{common,penalties}/*.py`.

`build_snapshot` is new here: `app/repositories/common/fulfillment.py`'s
module docstring explicitly deferred composing the `OrderSnapshot` to this
phase, once two schema gaps were resolved (both resolved by the approved
Phase 1 plan and Phase 2's own flags): `unit_price` now lives on
`purchase_order_line`, and a PO can have more than one line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.repositories.common.fulfillment import FulfillmentRepository
from app.repositories.common.master_data import MasterDataRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.projection import PenaltyProjectionRepository
from app.repositories.penalties.rule import PenaltyRuleRepository
from app.services.penalties.projection.engine import ProjectionEngine
from app.services.penalties.projection.types import (
    AppointmentStatus,
    OrderSnapshot,
    ProductionStatus,
    ProjectionResult,
)
from app.utils.clock import utc_today


@dataclass
class ProjectionService:
    purchase_orders: PurchaseOrderRepository
    fulfillment: FulfillmentRepository
    rules: PenaltyRuleRepository
    master_data: MasterDataRepository
    projections: PenaltyProjectionRepository

    def build_snapshot(self, purchase_order_id: UUID, projection_date: date) -> OrderSnapshot:
        """Assemble a projection-ready `OrderSnapshot` from a purchase
        order's current lines/facts.

        The pure-calc engine's snapshot type stays single-line/scalar (see
        `app.services.penalties.projection.types`'s module docstring) --
        that contract does not change in this pass. Since
        `common.purchase_order_line` is normalized (a PO can have multiple
        lines, each with its own `ordered_quantity`/`unit_price`), this
        method aggregates a PO's lines into that same scalar shape:
        `order_qty` is the sum of every line's `ordered_quantity`, and
        `unit_price` is the quantity-weighted average unit price
        (`sum(qty * price) / sum(qty)`). This is exactly equivalent to the
        old single-line behavior when a PO has one line (the only case
        that exists in the four worked-example scenarios today), and is a
        defensible, documented approximation for a true multi-line PO --
        true multi-line/multi-SKU penalty-rule differentiation (e.g. pricing
        each line against its own rule set) is out of scope for this pass.

        `confirmed_qty`/`demand_exception_flagged` are true per-line facts
        (each `order_confirmation_line`/`demand_exception` row FKs to one
        `purchase_order_line`), so those aggregate honestly across every
        line (summed / any-line-flagged respectively) rather than
        approximating. `production_status` has no such honest aggregation
        available -- `production_schedule` is keyed by `(material_id,
        plant_id)`, a single pair, not a per-line list -- so it is read off
        the PO's first line (by `line_number`) only; a true multi-material
        PO would need per-line production status, which is the same
        out-of-scope multi-SKU differentiation noted above.
        """
        purchase_order = self.purchase_orders.require_purchase_order(purchase_order_id)
        lines = self.purchase_orders.list_lines(purchase_order_id)
        if not lines:
            raise BusinessRuleError(
                code="NO_ACTIVE_RULES",
                message=f"Purchase order {purchase_order_id} has no lines to project a snapshot from.",
            )

        order_qty = sum(line["ordered_quantity"] for line in lines)
        if order_qty > 0:
            unit_price = sum(line["ordered_quantity"] * line["unit_price"] for line in lines) / order_qty
        else:
            unit_price = 0.0

        confirmed_qty = 0.0
        demand_exception_flagged = False
        for line in lines:
            latest_confirmation = self.fulfillment.get_latest_confirmation_line_not_after(
                line["id"], projection_date
            )
            if latest_confirmation is not None:
                confirmed_qty += latest_confirmation["confirmed_quantity"]
            if self.fulfillment.has_open_demand_exception_not_after(line["id"], projection_date):
                demand_exception_flagged = True

        production_status = ProductionStatus.ON_TRACK
        primary_line = lines[0]
        if primary_line["material_id"] is not None and primary_line["plant_id"] is not None:
            latest_schedule = self.fulfillment.get_latest_production_schedule_not_after(
                primary_line["material_id"], primary_line["plant_id"], projection_date
            )
            if latest_schedule is not None:
                production_status = ProductionStatus(latest_schedule["status"])

        expected_ship_date = None
        actual_ship_date = None
        appointment_status = AppointmentStatus.SCHEDULED
        expected_transit_days = 2
        carrier_reliability_score = 90.0
        shipment = self.fulfillment.get_latest_shipment_for_purchase_order_not_after(
            purchase_order_id, projection_date
        )
        if shipment is not None:
            expected_ship_date = shipment["expected_ship_date"]
            actual_ship_date = shipment["actual_ship_date"]
            if shipment["appointment_status"]:
                appointment_status = AppointmentStatus(shipment["appointment_status"])
            if shipment["expected_transit_days"] is not None:
                expected_transit_days = shipment["expected_transit_days"]
            if shipment["carrier_id"] is not None:
                carrier = self.master_data.get_carrier(shipment["carrier_id"])
                if carrier is not None:
                    carrier_reliability_score = carrier["historical_reliability_score"]

        requested_delivery_date = (
            purchase_order["current_delivery_date"] or purchase_order["requested_delivery_date"]
        )
        required_ship_date = (
            purchase_order["current_required_ship_date"] or purchase_order["required_ship_date"]
        )

        return OrderSnapshot(
            order_id=str(purchase_order_id),
            projection_date=projection_date,
            order_qty=round(order_qty),
            unit_price=unit_price,
            requested_delivery_date=requested_delivery_date,
            required_ship_date=required_ship_date,
            confirmed_qty=round(confirmed_qty),
            production_status=production_status,
            demand_exception_flagged=demand_exception_flagged,
            expected_ship_date=expected_ship_date,
            actual_ship_date=actual_ship_date,
            appointment_status=appointment_status,
            carrier_reliability_score=carrier_reliability_score,
            expected_transit_days=expected_transit_days,
        )

    def run_for_purchase_order(
        self,
        purchase_order_id: UUID,
        projection_date: date | None = None,
        stacking_mode_override: str | None = None,
    ) -> ProjectionResult:
        purchase_order = self.purchase_orders.get_purchase_order(purchase_order_id)
        if purchase_order is None:
            raise NotFoundError(
                code="PO_NOT_FOUND",
                message=f"No purchase order found with purchase_order_id={purchase_order_id}",
            )

        projection_date = projection_date or utc_today()
        snapshot = self.build_snapshot(purchase_order_id, projection_date)
        rule_list = self.rules.list_rules_for_retailer(purchase_order["retailer_id"])
        if not rule_list:
            raise BusinessRuleError(
                code="NO_ACTIVE_RULES",
                message=(
                    f"No active penalty rules for retailer {purchase_order['retailer_id']} "
                    f"(purchase_order {purchase_order_id})"
                ),
            )

        stacking_mode = stacking_mode_override or self.master_data.get_stacking_mode(
            purchase_order["retailer_id"]
        )
        result = ProjectionEngine().project(snapshot, rule_list, stacking_mode=stacking_mode)
        ids_by_rule_id = self.projections.save_result(purchase_order_id, result)
        # Stitch each violation's own persisted `penalty_projection.id` back
        # onto it -- the only way `POST /penalties/projections` can hand a
        # client that row's id in the response (see `ViolationProjection.id`'s
        # docstring); `save_result` itself stays return-shaped as a plain
        # rule_id -> id mapping rather than mutating `result` directly, so it
        # has no dependency on the pure-engine dataclass being mutable.
        for v in result.violations:
            v.id = ids_by_rule_id[v.rule_id]
        return result

    def run_for_all_open(
        self,
        projection_date: date | None = None,
        stacking_mode_override: str | None = None,
    ) -> list[ProjectionResult]:
        results = []
        for purchase_order in self.purchase_orders.list_purchase_orders(order_status="OPEN"):
            results.append(
                self.run_for_purchase_order(purchase_order["id"], projection_date, stacking_mode_override)
            )
        return results
