"""Orchestrates one projection run: assemble the snapshot, load the retailer's rules, run the pure engine, persist, return the result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.core.exceptions import NoActiveRulesError, OrderNotFoundError
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.master_data import MasterDataRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.services.fine_projection import ProjectionResult, project_order


@dataclass
class ProjectionService:
    orders: OrderRepository
    rules: FineRuleRepository
    master_data: MasterDataRepository
    projections: ProjectionRepository

    def run_for_order(
        self,
        order_id: str,
        projection_date: date | None = None,
        stacking_mode_override: str | None = None,
    ) -> ProjectionResult:
        order = self.orders.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        projection_date = projection_date or datetime.now(UTC).date()
        snapshot = self.orders.build_snapshot(order_id, projection_date)
        rule_list = self.rules.list_rules_for_retailer(order["retailer_id"])
        if not rule_list:
            raise NoActiveRulesError(
                f"No active fine rules for retailer {order['retailer_id']!r} (order {order_id!r})"
            )

        stacking_mode = stacking_mode_override or self.master_data.get_stacking_mode(order["retailer_id"])
        result = project_order(snapshot, rule_list, stacking_mode=stacking_mode)
        self.projections.save_result(result)
        return result

    def run_for_all_open(
        self,
        projection_date: date | None = None,
        stacking_mode_override: str | None = None,
    ) -> list[ProjectionResult]:
        results = []
        for order in self.orders.list_orders(order_status="OPEN"):
            results.append(self.run_for_order(order["order_id"], projection_date, stacking_mode_override))
        return results
