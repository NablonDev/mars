"""Orchestrates master-data, fine_projection, and fine_mitigation seeding
as one idempotent operation, and replays the four worked-example scenarios
day by day."""

from __future__ import annotations

from dataclasses import dataclass

from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_mitigation.mitigation import MitigationRepository
from app.repositories.fine_projection.po_delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.job_queue import JobQueueRepository
from app.repositories.order import OrderRepository
from app.services.fine_projection.po_delivery_change import PoDeliveryChangeRequestService
from app.services.fine_projection.service import FineProjectionService
from app.services.seeding import fine_mitigation as fine_mitigation_seed
from app.services.seeding import fine_projection as fine_projection_seed
from app.services.seeding import master_data as master_data_seed


@dataclass
class FineSeedingService:
    master_data: MasterDataRepository
    rules: FineRuleRepository
    orders: OrderRepository
    projection_service: FineProjectionService
    mitigation: MitigationRepository
    po_delivery_change_service: PoDeliveryChangeRequestService
    po_delivery_change_requests: PoDeliveryChangeRequestRepository
    job_queue: JobQueueRepository

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
        counts.update(fine_projection_seed.seed(self.rules, self.orders))
        counts.update(fine_mitigation_seed.seed(self.mitigation))
        return counts

    def _truncate_seeded_tables(self) -> None:
        """FK-safe truncate order: children before the parents they
        reference.

        mitigation_input/mitigation_option/mitigation_summary,
        po_delivery_change_request, job_item/job_run, and every sales_order
        fact/outcome table (order_confirmation, production_schedule,
        shipment, demand_exception, actual_fine, projected_fine,
        projection_summary) must be cleared before sales_order itself;
        sales_order and fine_rule must both be cleared before the
        retailer/sku/location/carrier master data they reference. See the
        individual repositories' truncate_all() docstrings for exactly what
        each step covers."""
        self.mitigation.truncate_all()
        self.po_delivery_change_requests.truncate_all()
        self.job_queue.truncate_all()
        self.orders.truncate_all()
        self.rules.truncate_all()
        self.master_data.truncate_all()

    def simulate_daily_run(self) -> list[dict]:
        """Walks all four scenarios day by day: writes each day's facts,
        runs the projection, and marks the order DELIVERED after its final
        day. Also interleaves one PO delivery-change-request negotiation
        outcome per order -- see fine_projection_seed.simulate_daily_run's
        docstring."""
        return fine_projection_seed.simulate_daily_run(
            self.orders, self.projection_service, self.po_delivery_change_service
        )
