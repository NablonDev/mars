"""Repository for `penalties.po_delivery_change_request` --
the request/response lifecycle history
`PoDeliveryChangeRequestService` reads and writes. Historized,
one row per request, same "latest row per PO" convention as
`order_confirmation`/`shipment`.

Was `app/repositories/fine_projection/po_delivery_change_request.py`
(`PoDeliveryChangeRequestRepository`); FK now points at the surrogate
`common.purchase_order.id` rather than the business-key `sales_order.order_id`.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PoDeliveryChangeRequest


def _to_dict(row: PoDeliveryChangeRequest) -> dict:
    return {
        "id": row.id,
        "request_id": row.request_id,
        "purchase_order_id": row.purchase_order_id,
        "reason_code": row.reason_code,
        "requested_at": row.requested_at,
        "baseline_delivery_date": row.baseline_delivery_date,
        "proposed_delivery_date": row.proposed_delivery_date,
        "expires_at": row.expires_at,
        "status": row.status,
        "retailer_response_date": row.retailer_response_date,
        "countered_delivery_date": row.countered_delivery_date,
        "resolved_at": row.resolved_at,
        "response_payload": row.response_payload,
        "notes": row.notes,
    }


class PoDeliveryChangeRequestRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        request_id: str,
        purchase_order_id: UUID,
        reason_code: str,
        requested_at: datetime,
        baseline_delivery_date: date,
        proposed_delivery_date: date,
        expires_at: datetime,
        notes: str | None = None,
    ) -> dict:
        row = PoDeliveryChangeRequest(
            request_id=request_id,
            purchase_order_id=purchase_order_id,
            reason_code=reason_code,
            requested_at=requested_at,
            baseline_delivery_date=baseline_delivery_date,
            proposed_delivery_date=proposed_delivery_date,
            expires_at=expires_at,
            status="PENDING",
            notes=notes,
        )
        self._session.add(row)
        self._session.flush()
        return _to_dict(row)

    def get_by_request_id(self, request_id: str) -> dict | None:
        row = self._get_row(request_id)
        return _to_dict(row) if row is not None else None

    def _get_row(self, request_id: str) -> PoDeliveryChangeRequest | None:
        return self._session.scalars(
            select(PoDeliveryChangeRequest).where(PoDeliveryChangeRequest.request_id == request_id)
        ).first()

    def find_active_for_purchase_order(self, purchase_order_id: UUID) -> dict | None:
        """Latest PENDING request for the PO, if any -- "active" means not
        yet responded to and not yet expired. Used to enforce the
        one-active-request-per-PO rule in
        `PoDeliveryChangeRequestService.create_request`."""
        row = self._session.scalars(
            select(PoDeliveryChangeRequest)
            .where(
                PoDeliveryChangeRequest.purchase_order_id == purchase_order_id,
                PoDeliveryChangeRequest.status == "PENDING",
            )
            .order_by(PoDeliveryChangeRequest.requested_at.desc())
            .limit(1)
        ).first()
        return _to_dict(row) if row is not None else None

    def find_expired(self, as_of: datetime) -> list[dict]:
        """PENDING requests whose `expires_at` has passed as_of -- the sweep's input."""
        rows = self._session.scalars(
            select(PoDeliveryChangeRequest).where(
                PoDeliveryChangeRequest.status == "PENDING",
                PoDeliveryChangeRequest.expires_at <= as_of,
            )
        ).all()
        return [_to_dict(r) for r in rows]

    def list_history(self, purchase_order_id: UUID) -> list[dict]:
        rows = self._session.scalars(
            select(PoDeliveryChangeRequest)
            .where(PoDeliveryChangeRequest.purchase_order_id == purchase_order_id)
            .order_by(PoDeliveryChangeRequest.requested_at.asc())
        ).all()
        return [_to_dict(r) for r in rows]

    def record_response(
        self,
        request_id: str,
        status: str,
        retailer_response_date: date,
        resolved_at: datetime,
        countered_delivery_date: date | None = None,
        response_payload: dict | None = None,
    ) -> dict:
        row = self._get_row(request_id)
        if row is None:
            raise ValueError(f"No PO delivery change request found with request_id={request_id!r}")

        row.status = status
        row.retailer_response_date = retailer_response_date
        row.countered_delivery_date = countered_delivery_date
        row.resolved_at = resolved_at
        row.response_payload = response_payload
        self._session.flush()
        return _to_dict(row)

    def mark_expired(self, request_id: str, resolved_at: datetime) -> dict:
        row = self._get_row(request_id)
        if row is None:
            raise ValueError(f"No PO delivery change request found with request_id={request_id!r}")

        row.status = "EXPIRED"
        row.resolved_at = resolved_at
        self._session.flush()
        return _to_dict(row)

    def truncate_all(self) -> None:
        """Deletes every po_delivery_change_request row -- it
        FKs to purchase_order, so it must be cleared before
        PurchaseOrderRepository.truncate_all() clears purchase_order
        itself."""
        self._session.execute(delete(PoDeliveryChangeRequest))
        self._session.flush()
