"""Business logic for the PO delivery-date change request/response lifecycle.

Was `app/services/fine_projection/po_delivery_change.py`'s
`PoDeliveryChangeRequestService` (renamed
`PoDeliveryChangeRequestService`, folder-split per the approved
plan). Rewritten against the Phase 2 `common`/`penalties` repositories.

Ops fires a request asking a retailer for more delivery time, the retailer's decision
is recorded (mock/manual entry -- no inbound webhook in this pass, see the design plan),
and `PurchaseOrder.current_delivery_date`/`current_required_ship_date` are updated on
ACCEPTED/COUNTERED. In every terminal case (ACCEPTED/COUNTERED/REJECTED/EXPIRED) the
existing projection engine is re-triggered via `ProjectionService.run_for_purchase_order`
so the same-day projection reflects the outcome instead of waiting for tomorrow's batch
-- see the design plan's "no shadow mitigation duplication" decision.

The lead-time/SLA/threshold policy is per-retailer business data (`Retailer.extension_*`
columns, read through `MasterDataRepository.get_extension_policy`), not app config --
two retailers can have different lead-time and SLA rules.

`PurchaseOrder.negotiation_status` is a denormalized current-state string with exactly
one writer: this service. See the column comment on `PurchaseOrder.negotiation_status`
and `PurchaseOrderRepository.update_negotiation_status`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.repositories.common.delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.common.master_data import MasterDataRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.services.penalties.projection.service import ProjectionService
from app.utils.clock import utc_now_naive

_TERMINAL_DECISIONS = {"ACCEPTED", "COUNTERED", "REJECTED"}


def _utcnow() -> datetime:
    """Naive UTC, matching this table's naive DateTime columns -- same convention
    as `app/repositories/process/job_queue.py`'s `_utcnow()`."""
    return utc_now_naive()


def _new_request_id() -> str:
    """External-system correlation key, generated here (not by the caller)
    so every request gets one regardless of entry point (API, seeding
    replay, the ops CLI). Not API-visible -- see this module's docstring
    and `PoDeliveryChangeRequestRepository.get_by_id` being the sole
    lookup method."""
    return f"ext_{uuid4().hex[:12]}"


@dataclass
class PoDeliveryChangeRequestService:
    purchase_orders: PurchaseOrderRepository
    delivery_change_requests: PoDeliveryChangeRequestRepository
    projection_service: ProjectionService
    master_data: MasterDataRepository

    def create_request(
        self,
        purchase_order_id: UUID,
        reason_code: str,
        proposed_delivery_date: date,
        notes: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """`now` is injectable so day-by-day scenario replay (seeding, tests) can
        anchor requested_at/expires_at to a mock as-of date instead of wall-clock
        time -- same pattern as `ProjectionService.run_for_purchase_order`'s
        `projection_date` override."""
        purchase_order = self.purchase_orders.require_purchase_order(purchase_order_id)

        active = self.delivery_change_requests.find_active_for_purchase_order(purchase_order_id)
        if active is not None:
            raise ConflictError(
                code="ACTIVE_PO_DELIVERY_CHANGE_REQUEST_EXISTS",
                message=(
                    f"Purchase order {purchase_order_id} already has an active PO "
                    f"delivery-change request ({active['id']})"
                ),
            )

        policy = self.master_data.get_extension_policy(purchase_order["retailer_id"])

        now = now or _utcnow()
        current_required_ship_date = (
            purchase_order["current_required_ship_date"] or purchase_order["required_ship_date"]
        )
        lead_days = (current_required_ship_date - now.date()).days
        if lead_days < policy["min_lead_days"]:
            raise BusinessRuleError(
                code="PO_DELIVERY_CHANGE_LEAD_TIME_ERROR",
                message=(
                    f"Purchase order {purchase_order_id} is only {lead_days} day(s) from its required "
                    f"ship date ({current_required_ship_date.isoformat()}); minimum lead time to request "
                    f"a delivery-date change is {policy['min_lead_days']} day(s)."
                ),
            )

        baseline_delivery_date = (
            purchase_order["current_delivery_date"] or purchase_order["requested_delivery_date"]
        )

        created = self.delivery_change_requests.create(
            purchase_order_id=purchase_order_id,
            reason_code=reason_code,
            requested_at=now,
            baseline_delivery_date=baseline_delivery_date,
            proposed_delivery_date=proposed_delivery_date,
            expires_at=now + timedelta(hours=policy["response_sla_hours"]),
            request_id=_new_request_id(),
            notes=notes,
        )
        self.purchase_orders.update_negotiation_status(purchase_order_id, "PENDING")
        return created

    def record_response(
        self,
        delivery_change_request_id: UUID,
        decision: str,
        countered_delivery_date: date | None = None,
        now: datetime | None = None,
    ) -> dict:
        """`now` is injectable for the same reason as create_request's -- see there."""
        row = self.delivery_change_requests.get_by_id(delivery_change_request_id)
        if row is None:
            raise NotFoundError(
                code="PO_DELIVERY_CHANGE_REQUEST_NOT_FOUND",
                message=f"No PO delivery-change request found with id={delivery_change_request_id}",
            )

        if row["status"] != "PENDING":
            raise ValidationError(
                code="INVALID_PO_DELIVERY_CHANGE_RESPONSE",
                message=(
                    f"PO delivery-change request {delivery_change_request_id} is not PENDING "
                    f"(status={row['status']!r}); a response has already been recorded, or it has "
                    "already expired."
                ),
            )

        if decision not in _TERMINAL_DECISIONS:
            raise ValidationError(
                code="INVALID_PO_DELIVERY_CHANGE_RESPONSE",
                message=f"decision must be one of {sorted(_TERMINAL_DECISIONS)}, got {decision!r}",
            )

        if decision == "COUNTERED":
            if countered_delivery_date is None:
                raise ValidationError(
                    code="INVALID_PO_DELIVERY_CHANGE_RESPONSE",
                    message="decision=COUNTERED requires countered_delivery_date",
                )
            if not (row["baseline_delivery_date"] < countered_delivery_date < row["proposed_delivery_date"]):
                raise ValidationError(
                    code="INVALID_PO_DELIVERY_CHANGE_RESPONSE",
                    message=(
                        "countered_delivery_date must fall strictly between "
                        f"baseline_delivery_date ({row['baseline_delivery_date'].isoformat()}) "
                        f"and proposed_delivery_date ({row['proposed_delivery_date'].isoformat()})"
                    ),
                )
        elif countered_delivery_date is not None:
            raise ValidationError(
                code="INVALID_PO_DELIVERY_CHANGE_RESPONSE",
                message="countered_delivery_date is only valid when decision=COUNTERED",
            )

        now = now or _utcnow()
        updated = self.delivery_change_requests.record_response(
            delivery_change_request_id=delivery_change_request_id,
            status=decision,
            retailer_response_date=now.date(),
            resolved_at=now,
            countered_delivery_date=countered_delivery_date,
        )

        purchase_order_id = row["purchase_order_id"]
        if decision in ("ACCEPTED", "COUNTERED"):
            if decision == "COUNTERED":
                assert countered_delivery_date is not None  # enforced by the COUNTERED branch above
                new_delivery_date = countered_delivery_date
            else:
                new_delivery_date = row["proposed_delivery_date"]
            # Shift required_ship_date by the same delta as the delivery-date change,
            # preserving the existing transit-day gap (design plan, decision 2).
            delta = new_delivery_date - row["baseline_delivery_date"]
            purchase_order = self.purchase_orders.require_purchase_order(purchase_order_id)
            current_required_ship_date = (
                purchase_order["current_required_ship_date"] or purchase_order["required_ship_date"]
            )
            self.purchase_orders.update_current_dates(
                purchase_order_id,
                current_delivery_date=new_delivery_date,
                current_required_ship_date=current_required_ship_date + delta,
            )

        self.purchase_orders.update_negotiation_status(purchase_order_id, decision)
        self.projection_service.run_for_purchase_order(purchase_order_id, now.date())
        return updated

    def expire_stale(self, as_of: datetime | None = None) -> list[dict]:
        resolved_as_of = as_of or _utcnow()
        expired = self.delivery_change_requests.find_expired(resolved_as_of)

        results = []
        for row in expired:
            updated = self.delivery_change_requests.mark_expired(row["id"], resolved_as_of)
            # No PurchaseOrder date change -- current_delivery_date is already
            # untouched while PENDING, so the existing projection cycle already
            # is the fallback plan. negotiation_status still moves to EXPIRED.
            self.purchase_orders.update_negotiation_status(row["purchase_order_id"], "EXPIRED")
            self.projection_service.run_for_purchase_order(row["purchase_order_id"], resolved_as_of.date())
            results.append(updated)
        return results

    def list_history(self, purchase_order_id: UUID | None = None) -> list[dict]:
        """`purchase_order_id` given: unchanged, one PO's full history.
        Omitted: every request across every PO -- backs
        `GET /delivery-change-requests` with no filter."""
        return self.delivery_change_requests.list_history(purchase_order_id)

    def get_by_id(self, delivery_change_request_id: UUID) -> dict:
        """Standalone fetch by the surrogate `id` -- backs
        `GET /delivery-change-requests/{delivery_change_request_id}`. Same
        not-found shape `record_response` already raises for an unknown
        id."""
        row = self.delivery_change_requests.get_by_id(delivery_change_request_id)
        if row is None:
            raise NotFoundError(
                code="PO_DELIVERY_CHANGE_REQUEST_NOT_FOUND",
                message=f"No PO delivery-change request found with id={delivery_change_request_id}",
            )
        return row
