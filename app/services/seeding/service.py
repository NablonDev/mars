"""Orchestrates master-data, penalty-projection, and penalty-mitigation
seeding as one idempotent operation, and replays the four worked-example
scenarios day by day.

Was `app/services/seeding/service.py`'s `FineSeedingService` (renamed
`PenaltySeedingService`). Rewritten against the Phase 2 `common`/
`penalties`/`process` repositories.

`_truncate_seeded_tables`'s FK-safe ordering is new, hand-derived work this
phase had to do (Phase 2 didn't -- and couldn't -- resolve it, since two of
the truncate methods it calls, `PenaltyProjectionRepository.truncate_all`/
`ActualPenaltyRepository.truncate_all`, didn't exist until this phase added
them). See `force-seeding-error.txt` at the repo root for the exact class
of bug a wrong order here reproduces (a `job_item` row still referencing
a row this deletes).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.repositories.common.delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.common.fulfillment import FulfillmentRepository
from app.repositories.common.master_data import MasterDataRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.dispute import PenaltyDisputeRepository
from app.repositories.penalties.job_context import (
    PenaltyJobItemContextRepository,
    PenaltyJobRunContextRepository,
)
from app.repositories.penalties.mitigation import MitigationInputRepository
from app.repositories.penalties.projection import ActualPenaltyRepository, PenaltyProjectionRepository
from app.repositories.penalties.rule import PenaltyRuleRepository
from app.repositories.penalties.summary import PenaltySummaryRepository
from app.repositories.process.job_queue import JobQueueRepository
from app.services.penalties.delivery_change import PoDeliveryChangeRequestService
from app.services.penalties.projection.service import ProjectionService
from app.services.seeding import dispute as dispute_seed
from app.services.seeding import master_data as master_data_seed
from app.services.seeding import mitigation as mitigation_seed
from app.services.seeding import projection as projection_seed


@dataclass
class PenaltySeedingService:
    master_data: MasterDataRepository
    rules: PenaltyRuleRepository
    purchase_orders: PurchaseOrderRepository
    fulfillment: FulfillmentRepository
    projection_service: ProjectionService
    mitigation_inputs: MitigationInputRepository
    delivery_change_service: PoDeliveryChangeRequestService
    delivery_change_requests: PoDeliveryChangeRequestRepository
    penalty_summaries: PenaltySummaryRepository
    penalty_projections: PenaltyProjectionRepository
    actual_penalties: ActualPenaltyRepository
    disputes: PenaltyDisputeRepository
    job_queue: JobQueueRepository
    penalty_job_item_context: PenaltyJobItemContextRepository
    penalty_job_run_context: PenaltyJobRunContextRepository

    def seed_master_data(self, force: bool = False) -> dict:
        """Idempotent by default: safe to call repeatedly, skips anything
        that already exists rather than erroring on a duplicate key.

        force=True instead truncates every seeded table first and reseeds
        from scratch -- a full reset, not a per-field upsert. Meant for a
        demo/seed environment, not as a production data-safety feature."""
        if force:
            self._truncate_seeded_tables()

        counts: dict[str, int] = {}
        counts.update(master_data_seed.seed(self.master_data))
        counts.update(
            projection_seed.seed(self.rules, self.purchase_orders, self.fulfillment, self.master_data)
        )
        counts.update(mitigation_seed.seed(self.mitigation_inputs, self.purchase_orders))
        counts.update(self.seed_disputes())
        return counts

    def seed_disputes(self) -> dict[str, int]:
        """Additive dispute-resolution fixture data (new rules, new
        retailers, eight dedicated purchase orders + fulfillment facts +
        `actual_penalty` charges) -- never touches the four existing
        worked-example POs or their rule/fulfillment data. Idempotent, same
        posture as every other `seed_*` step; called from
        `seed_master_data()` and separately callable for tests that only
        need the dispute fixtures."""
        return dispute_seed.seed(
            self.rules,
            self.purchase_orders,
            self.fulfillment,
            self.master_data,
            self.actual_penalties,
        )

    def _truncate_seeded_tables(self) -> None:
        """FK-safe truncate order: children before the parents they
        reference.

        `penalty_summary`, `penalty_dispute`, `penalty_job_item_context`/
        `penalty_job_run_context`, `mitigation_input`/`mitigation_option`,
        `po_delivery_change_request`, `penalty_projection`,
        `actual_penalty`, `job_item`/`job_run`, and every fulfillment fact
        (`order_confirmation`, `production_schedule`, `shipment`,
        `demand_exception`, ...) must be cleared before `purchase_order`
        itself; `purchase_order` and `penalty_rule` must both be cleared
        before the retailer/material/plant/carrier master data they
        reference. `penalty_summary` FKs only to `purchase_order` --
        `penalty_job_item_context` FKs to `purchase_order` too and does not
        FK to `penalty_dispute` at all (its `DISPUTE_SUMMARY_REGEN` rows
        key off `process.job_item.metadata_json` instead, see
        `app.models.penalties.job_context.PenaltyJobItemContext`'s
        docstring). `penalty_summaries.truncate_all()` still runs before
        `disputes.truncate_all()` for tidiness, though nothing FKs between
        them any more; `disputes.truncate_all()` in turn FKs to
        `actual_penalty`/`penalty_rule`/`purchase_order` and so must run
        before those. See the individual repositories' `truncate_all()`
        docstrings for exactly what each step covers."""
        self.penalty_summaries.truncate_all()
        self.penalty_job_item_context.truncate_all()
        self.disputes.truncate_all()
        self.penalty_job_run_context.truncate_all()
        self.mitigation_inputs.truncate_all()  # also clears mitigation_option
        self.delivery_change_requests.truncate_all()
        self.penalty_projections.truncate_all()
        self.actual_penalties.truncate_all()
        self.job_queue.truncate_all()
        self.rules.truncate_all()
        self.fulfillment.truncate_all()
        self.purchase_orders.truncate_all()
        self.master_data.truncate_all()

    def simulate_daily_run(self) -> list[dict]:
        """Walks all four scenarios day by day: writes each day's facts,
        runs the projection, and marks the order DELIVERED after its final
        day. Also interleaves one PO delivery-change-request negotiation
        outcome per order -- see `projection_seed.simulate_daily_run`'s
        docstring."""
        return projection_seed.simulate_daily_run(
            self.purchase_orders,
            self.fulfillment,
            self.master_data,
            self.projection_service,
            self.delivery_change_service,
        )
