"""Business logic for the PO delivery-date change request/response lifecycle.

Ops fires a request asking a retailer for more delivery time, the retailer's decision
is recorded (mock/manual entry -- no inbound webhook in this pass, see the design plan),
and `Order.current_delivery_date`/`current_required_ship_date` are updated on ACCEPTED/
COUNTERED. In every terminal case (ACCEPTED/COUNTERED/REJECTED/EXPIRED) the existing
projection engine is re-triggered via `FineProjectionService.run_for_order` so the
same-day projection reflects the outcome instead of waiting for tomorrow's batch --
see the design plan's "no shadow mitigation duplication" decision.

The lead-time/SLA/threshold policy is per-retailer business data (`Retailer.extension_*`
columns, read through `MasterDataRepository.get_extension_policy`), not app config --
two retailers can have different lead-time and SLA rules.

`Order.negotiation_status` is a denormalized current-state string with exactly one
writer: this service. See the column comment on `Order.negotiation_status` and
`OrderRepository.update_negotiation_status`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.core.exceptions import (
    ActivePoDeliveryChangeRequestExistsError,
    InvalidPoDeliveryChangeResponseError,
    PoDeliveryChangeLeadTimeError,
    PoDeliveryChangeRequestNotFoundError,
)
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_projection.po_delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.order import OrderRepository
from app.services.fine_projection.service import FineProjectionService

_TERMINAL_DECISIONS = {"ACCEPTED", "COUNTERED", "REJECTED"}


def _new_request_id() -> str:
    return f"ext_{uuid4().hex[:12]}"


def _utcnow() -> datetime:
    """Naive UTC, matching this table's naive DateTime columns -- same convention
    as app/repositories/job_queue.py's _utcnow()."""
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class PoDeliveryChangeRequestService:
    orders: OrderRepository
    po_delivery_change_requests: PoDeliveryChangeRequestRepository
    projection_service: FineProjectionService
    master_data: MasterDataRepository

    def create_request(
        self,
        order_id: str,
        reason_code: str,
        proposed_delivery_date: date,
        notes: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """`now` is injectable so day-by-day scenario replay (seeding, tests) can
        anchor requested_at/expires_at to a mock as-of date instead of wall-clock
        time -- same pattern as FineProjectionService.run_for_order's
        `projection_date` override."""
        order = self.orders.require_order(order_id)

        active = self.po_delivery_change_requests.find_active_for_order(order_id)
        if active is not None:
            raise ActivePoDeliveryChangeRequestExistsError(order_id, active["request_id"])

        policy = self.master_data.get_extension_policy(order["retailer_id"])

        now = now or _utcnow()
        current_required_ship_date = order["current_required_ship_date"] or order["required_ship_date"]
        lead_days = (current_required_ship_date - now.date()).days
        if lead_days < policy["min_lead_days"]:
            raise PoDeliveryChangeLeadTimeError(
                f"Order {order_id!r} is only {lead_days} day(s) from its required ship date "
                f"({current_required_ship_date.isoformat()}); minimum lead time to request a "
                f"delivery-date change is {policy['min_lead_days']} day(s)."
            )

        baseline_delivery_date = order["current_delivery_date"] or order["requested_delivery_date"]

        created = self.po_delivery_change_requests.create(
            request_id=_new_request_id(),
            order_id=order_id,
            reason_code=reason_code,
            requested_at=now,
            baseline_delivery_date=baseline_delivery_date,
            proposed_delivery_date=proposed_delivery_date,
            expires_at=now + timedelta(hours=policy["response_sla_hours"]),
            notes=notes,
        )
        self.orders.update_negotiation_status(order_id, "PENDING")
        return created

    def record_response(
        self,
        request_id: str,
        decision: str,
        countered_delivery_date: date | None = None,
        now: datetime | None = None,
    ) -> dict:
        """`now` is injectable for the same reason as create_request's -- see there."""
        row = self.po_delivery_change_requests.get_by_request_id(request_id)
        if row is None:
            raise PoDeliveryChangeRequestNotFoundError(request_id)

        if row["status"] != "PENDING":
            raise InvalidPoDeliveryChangeResponseError(
                f"PO delivery-change request {request_id!r} is not PENDING (status={row['status']!r}); "
                "a response has already been recorded, or it has already expired."
            )

        if decision not in _TERMINAL_DECISIONS:
            raise InvalidPoDeliveryChangeResponseError(
                f"decision must be one of {sorted(_TERMINAL_DECISIONS)}, got {decision!r}"
            )

        if decision == "COUNTERED":
            if countered_delivery_date is None:
                raise InvalidPoDeliveryChangeResponseError(
                    "decision=COUNTERED requires countered_delivery_date"
                )
            if not (row["baseline_delivery_date"] < countered_delivery_date < row["proposed_delivery_date"]):
                raise InvalidPoDeliveryChangeResponseError(
                    "countered_delivery_date must fall strictly between "
                    f"baseline_delivery_date ({row['baseline_delivery_date'].isoformat()}) "
                    f"and proposed_delivery_date ({row['proposed_delivery_date'].isoformat()})"
                )
        elif countered_delivery_date is not None:
            raise InvalidPoDeliveryChangeResponseError(
                "countered_delivery_date is only valid when decision=COUNTERED"
            )

        now = now or _utcnow()
        updated = self.po_delivery_change_requests.record_response(
            request_id=request_id,
            status=decision,
            retailer_response_date=now.date(),
            resolved_at=now,
            countered_delivery_date=countered_delivery_date,
        )

        if decision in ("ACCEPTED", "COUNTERED"):
            if decision == "COUNTERED":
                assert countered_delivery_date is not None  # enforced by the COUNTERED branch above
                new_delivery_date = countered_delivery_date
            else:
                new_delivery_date = row["proposed_delivery_date"]
            # Shift required_ship_date by the same delta as the delivery-date change,
            # preserving the existing transit-day gap (design plan, decision 2).
            delta = new_delivery_date - row["baseline_delivery_date"]
            order = self.orders.require_order(row["order_id"])
            current_required_ship_date = order["current_required_ship_date"] or order["required_ship_date"]
            self.orders.update_current_dates(
                row["order_id"],
                current_delivery_date=new_delivery_date,
                current_required_ship_date=current_required_ship_date + delta,
            )

        self.orders.update_negotiation_status(row["order_id"], decision)
        self.projection_service.run_for_order(row["order_id"], now.date())
        return updated

    def expire_stale(self, as_of: datetime | None = None) -> list[dict]:
        resolved_as_of = as_of or _utcnow()
        expired = self.po_delivery_change_requests.find_expired(resolved_as_of)

        results = []
        for row in expired:
            updated = self.po_delivery_change_requests.mark_expired(row["request_id"], resolved_as_of)
            # No Order date change -- current_delivery_date is already untouched
            # while PENDING, so the existing projection cycle already is the
            # fallback plan. negotiation_status still moves to EXPIRED.
            self.orders.update_negotiation_status(row["order_id"], "EXPIRED")
            self.projection_service.run_for_order(row["order_id"], resolved_as_of.date())
            results.append(updated)
        return results

    def list_history(self, order_id: str) -> list[dict]:
        return self.po_delivery_change_requests.list_history(order_id)
