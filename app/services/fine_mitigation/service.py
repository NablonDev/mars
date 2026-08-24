"""Orchestrates one mitigation-ranking run: reload the order's already-persisted
projection for the day, load cause/cost inputs, run the pure engine, persist
the ranked options, return the result.

Mirrors app/services/fine_projection/service.py's shape. The key difference
from that service: mitigation evaluates against a projection that has
*already run and been persisted* (projected_fine, via
ProjectionRepository) -- it never calls
app.services.fine_projection.ProjectionEngine.project itself. This module
reconstructs the pure-engine ProjectionResult dataclass from the persisted
rows so app.services.fine_mitigation.engine.MitigationEngine (which is
typed against that dataclass, not a repository dict) can be called unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.core.exceptions import NoMitigationOptionsExistError, NoProjectionExistsError, OrderNotFoundError
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_mitigation.mitigation import MitigationRepository, MitigationResultRepository
from app.repositories.fine_projection.projection import ProjectionRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.order import OrderRepository
from app.services.fine_mitigation.engine import MitigationEngine
from app.services.fine_mitigation.types import MitigationOption
from app.services.fine_projection import (
    DELAY_VIOLATION_TYPES,
    SHORTAGE_VIOLATION_TYPES,
    ProjectionResult,
    ViolationProjection,
)


def _build_projection_result(
    order_id: str,
    projection_date: date,
    stacking_mode: str,
    day_rows: list[dict],
) -> ProjectionResult:
    """Reconstructs the pure-engine ProjectionResult from persisted
    projected_fine rows for one day, the same reconstruction
    FineProjectionSummaryService._build_daily_history performs for the
    LLM-context shape -- kept independent here since this needs the
    actual dataclass, not a Pydantic context row."""
    violations = [
        ViolationProjection(
            violation_type=row["violation_type"],
            rule_id=row["rule_id"],
            probability=row["failure_probability"],
            fine_if_realized=(
                round(row["projected_fine_amount"] / row["failure_probability"], 2)
                if row["failure_probability"]
                else 0.0
            ),
            expected_fine=row["projected_fine_amount"],
        )
        for row in day_rows
    ]

    if stacking_mode == "MAX":
        total = max((v.expected_fine for v in violations), default=0.0)
    else:
        total = sum(v.expected_fine for v in violations)

    shortage_probability = next(
        (r["failure_probability"] for r in day_rows if r["violation_type"] in SHORTAGE_VIOLATION_TYPES),
        0.0,
    )
    delay_probability = next(
        (r["failure_probability"] for r in day_rows if r["violation_type"] in DELAY_VIOLATION_TYPES),
        0.0,
    )
    days_to_delivery = day_rows[0]["days_to_delivery"] if day_rows else 0

    return ProjectionResult(
        order_id=order_id,
        projection_date=projection_date,
        days_to_delivery=days_to_delivery,
        shortage_probability=round(shortage_probability, 4),
        delay_probability=round(delay_probability, 4),
        violations=violations,
        total_expected_fine=round(total, 2),
        stacking_mode=stacking_mode,
    )


@dataclass
class FineMitigationService:
    orders: OrderRepository
    rules: FineRuleRepository
    master_data: MasterDataRepository
    projections: ProjectionRepository
    mitigation_inputs: MitigationRepository
    mitigation_results: MitigationResultRepository

    def run_for_order(
        self,
        order_id: str,
        projection_date: date | None = None,
    ) -> tuple[date, list[MitigationOption]]:
        order = self.orders.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        projection_date = projection_date or datetime.now(UTC).date()

        history = self.projections.list_history(order_id)
        day_rows = [row for row in history if row["projection_date"] == projection_date]
        if not day_rows:
            raise NoProjectionExistsError(
                f"No projection exists for order_id={order_id!r} on "
                f"projection_date={projection_date.isoformat()}. Run "
                f"POST /orders/{{order_id}}/projections for that date first."
            )

        # "Current" stacking mode, not whatever override (if any) produced
        # the historical projection -- same choice FineProjectionService's
        # own default path makes; individual violations' expected_fine
        # values are computed independently of stacking mode, so only the
        # aggregate total is affected.
        stacking_mode = self.master_data.get_stacking_mode(order["retailer_id"])
        projection = _build_projection_result(order_id, projection_date, stacking_mode, day_rows)

        snapshot = self.orders.build_snapshot(order_id, projection_date)
        rule_list = self.rules.list_rules_for_retailer(order["retailer_id"])
        inputs = self.mitigation_inputs.get_inputs(order_id)

        options = MitigationEngine().evaluate(snapshot, rule_list, projection, inputs)
        self.mitigation_results.save_results(order_id, projection_date, options)
        self.mitigation_results.commit()
        return projection_date, options

    def get_latest(self, order_id: str) -> tuple[date, list[dict]]:
        """Latest persisted, ranked mitigation options for an order."""
        order = self.orders.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        rows = self.mitigation_results.get_latest(order_id)
        if not rows:
            raise NoMitigationOptionsExistError(
                f"No mitigation options exist yet for order_id={order_id!r}. "
                "Run POST /orders/{order_id}/mitigation-options first."
            )
        return rows[0]["projection_date"], rows
