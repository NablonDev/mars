"""Repository for `common` schema fulfillment facts: order confirmations,
deliveries/shipments, production status, and demand exceptions. Was part of
`app/repositories/order.py`.

**Scope note (flagged, not silently resolved):** the old `OrderRepository`
also exposed `build_snapshot()`, composing a single order's confirmed
quantity/production status/shipment/demand-exception state into the
`OrderSnapshot` dataclass the projection engine consumes
(`app.services.fine_projection.types.OrderSnapshot`). That composition is
NOT reimplemented here. Two real schema gaps block a faithful port and need
a product decision before Phase 3 wires this up:

1. `OrderSnapshot.unit_price` has no home in the new schema --
   `purchase_order_line` carries no price/amount column at all (the ERP
   redesign doesn't model pricing anywhere yet).
2. `build_snapshot` was implicitly single-line-per-order; the new
   header/line split means a real implementation must decide which line
   (or which aggregation across lines) a projection is for.

Every idempotent/historized read method below is still provided
(latest-as-of-a-date lookups, full history, natural-key-checked fact
writers) -- composing them into a new `build_snapshot` is Phase 3's call
once those two gaps are resolved.

**Idempotency re-derivation (see the approved plan's risk checklist item
1):** `order_confirmation`, `delivery`, `shipment`, `production_order`, and
`demand_exception` all kept an explicit unique business-key column
(`confirmation_number`, `delivery_number`, `shipment_number`,
`production_order_number`, `exception_id`) migrated over from the old
schema, so their idempotent-insert pattern is unchanged in spirit.
`order_confirmation_line`/`delivery_line` get DB-enforced idempotency from
their new `UniqueConstraint`s. `production_schedule` alone has neither: it
carries no business-key column at all in the new model. Its natural key is
re-derived here as `(material_id, plant_id, status_at)` -- application-level
check-then-insert only (no DB unique index backs it, same posture the old
`production_id`-keyed check had before any DB constraint existed for it).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Delivery,
    DeliveryLine,
    DemandException,
    OrderConfirmation,
    OrderConfirmationLine,
    ProductionOrder,
    ProductionSchedule,
    Shipment,
)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time())


def _confirmation_to_dict(row: OrderConfirmation) -> dict:
    return {
        "id": row.id,
        "confirmation_number": row.confirmation_number,
        "purchase_order_id": row.purchase_order_id,
        "confirmation_date": row.confirmation_date,
        "status": row.status,
    }


def _confirmation_line_to_dict(row: OrderConfirmationLine) -> dict:
    return {
        "id": row.id,
        "order_confirmation_id": row.order_confirmation_id,
        "purchase_order_line_id": row.purchase_order_line_id,
        "confirmed_quantity": float(row.confirmed_quantity),
        "confirmed_delivery_date": row.confirmed_delivery_date,
        "cut_reason_code": row.cut_reason_code,
    }


def _delivery_to_dict(row: Delivery) -> dict:
    return {
        "id": row.id,
        "delivery_number": row.delivery_number,
        "purchase_order_id": row.purchase_order_id,
        "ship_from_plant_id": row.ship_from_plant_id,
        "ship_from_warehouse_id": row.ship_from_warehouse_id,
        "ship_to_location_id": row.ship_to_location_id,
        "delivery_status": row.delivery_status,
        "planned_delivery_date": row.planned_delivery_date,
        "actual_delivery_date": row.actual_delivery_date,
        "planned_ship_date": row.planned_ship_date,
        "actual_ship_date": row.actual_ship_date,
        "goods_issue_date": row.goods_issue_date,
    }


def _delivery_line_to_dict(row: DeliveryLine) -> dict:
    return {
        "id": row.id,
        "delivery_id": row.delivery_id,
        "purchase_order_line_id": row.purchase_order_line_id,
        "delivered_quantity": float(row.delivered_quantity),
        "uom": row.uom,
        "status": row.status,
    }


def _shipment_to_dict(row: Shipment) -> dict:
    return {
        "id": row.id,
        "shipment_number": row.shipment_number,
        "delivery_id": row.delivery_id,
        "carrier_id": row.carrier_id,
        "expected_ship_date": row.expected_ship_date,
        "actual_ship_date": row.actual_ship_date,
        "expected_delivery_date": row.expected_delivery_date,
        "actual_delivery_date": row.actual_delivery_date,
        "expected_transit_days": row.expected_transit_days,
        "appointment_status": row.appointment_status,
        "shipment_status": row.shipment_status,
        "recorded_at": row.recorded_at,
    }


def _production_order_to_dict(row: ProductionOrder) -> dict:
    return {
        "id": row.id,
        "production_order_number": row.production_order_number,
        "material_id": row.material_id,
        "plant_id": row.plant_id,
        "planned_quantity": float(row.planned_quantity) if row.planned_quantity is not None else None,
        "produced_quantity": float(row.produced_quantity) if row.produced_quantity is not None else None,
        "planned_start_date": row.planned_start_date,
        "actual_start_date": row.actual_start_date,
        "planned_end_date": row.planned_end_date,
        "actual_end_date": row.actual_end_date,
        "status": row.status,
    }


def _production_schedule_to_dict(row: ProductionSchedule) -> dict:
    return {
        "id": row.id,
        "production_order_id": row.production_order_id,
        "material_id": row.material_id,
        "plant_id": row.plant_id,
        "scheduled_quantity": float(row.scheduled_quantity) if row.scheduled_quantity is not None else None,
        "scheduled_start_at": row.scheduled_start_at,
        "scheduled_end_at": row.scheduled_end_at,
        "status": row.status,
        "status_at": row.status_at,
    }


def _demand_exception_to_dict(row: DemandException) -> dict:
    return {
        "id": row.id,
        "exception_id": row.exception_id,
        "purchase_order_line_id": row.purchase_order_line_id,
        "flagged_date": row.flagged_date,
        "resolved": row.resolved,
    }


class FulfillmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _insert_if_absent(self, model: type, filters: dict[str, Any], **fields: Any):
        """Insert a fact only when its natural key does not already exist.

        `filters` is one or more equality conditions identifying the
        natural key (a single unique column, or -- for production_schedule,
        which has none -- a composite tuple checked at the application
        level only)."""
        conditions = [getattr(model, key) == value for key, value in filters.items()]
        existing = self._session.scalars(select(model).where(*conditions)).first()
        if existing is not None:
            return existing

        row = model(**filters, **fields)
        self._session.add(row)
        self._session.flush()
        return row

    # ------------------------------------------------------------------
    # Order confirmation
    # ------------------------------------------------------------------

    def add_order_confirmation(
        self,
        confirmation_number: str,
        purchase_order_id: UUID,
        confirmation_date: datetime,
        status: str | None = None,
    ) -> dict:
        row = self._insert_if_absent(
            OrderConfirmation,
            {"confirmation_number": confirmation_number},
            purchase_order_id=purchase_order_id,
            confirmation_date=confirmation_date,
            status=status,
        )
        return _confirmation_to_dict(row)

    def add_order_confirmation_line(
        self,
        order_confirmation_id: UUID,
        purchase_order_line_id: UUID,
        confirmed_quantity: float,
        confirmed_delivery_date: date | None = None,
        cut_reason_code: str | None = None,
    ) -> dict:
        row = self._insert_if_absent(
            OrderConfirmationLine,
            {
                "order_confirmation_id": order_confirmation_id,
                "purchase_order_line_id": purchase_order_line_id,
            },
            confirmed_quantity=confirmed_quantity,
            confirmed_delivery_date=confirmed_delivery_date,
            cut_reason_code=cut_reason_code,
        )
        return _confirmation_line_to_dict(row)

    def list_confirmation_lines_for_line(self, purchase_order_line_id: UUID) -> list[dict]:
        """Full history, oldest first (by the parent confirmation's date)."""
        rows = self._session.scalars(
            select(OrderConfirmationLine)
            .join(OrderConfirmation, OrderConfirmation.id == OrderConfirmationLine.order_confirmation_id)
            .where(OrderConfirmationLine.purchase_order_line_id == purchase_order_line_id)
            .order_by(OrderConfirmation.confirmation_date.asc())
        ).all()
        return [_confirmation_line_to_dict(r) for r in rows]

    def get_latest_confirmation_line_not_after(
        self, purchase_order_line_id: UUID, as_of_date: date
    ) -> dict | None:
        row = self._session.scalars(
            select(OrderConfirmationLine)
            .join(OrderConfirmation, OrderConfirmation.id == OrderConfirmationLine.order_confirmation_id)
            .where(
                OrderConfirmationLine.purchase_order_line_id == purchase_order_line_id,
                OrderConfirmation.confirmation_date <= _end_of_day(as_of_date),
            )
            .order_by(OrderConfirmation.confirmation_date.desc())
            .limit(1)
        ).first()
        return _confirmation_line_to_dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # Delivery / shipment
    # ------------------------------------------------------------------

    def add_delivery(self, delivery_number: str, purchase_order_id: UUID, **fields: Any) -> dict:
        row = self._insert_if_absent(
            Delivery, {"delivery_number": delivery_number}, purchase_order_id=purchase_order_id, **fields
        )
        return _delivery_to_dict(row)

    def add_delivery_line(
        self, delivery_id: UUID, purchase_order_line_id: UUID, delivered_quantity: float, **fields: Any
    ) -> dict:
        row = self._insert_if_absent(
            DeliveryLine,
            {"delivery_id": delivery_id, "purchase_order_line_id": purchase_order_line_id},
            delivered_quantity=delivered_quantity,
            **fields,
        )
        return _delivery_line_to_dict(row)

    def list_deliveries_for_purchase_order(self, purchase_order_id: UUID) -> list[dict]:
        rows = self._session.scalars(
            select(Delivery)
            .where(Delivery.purchase_order_id == purchase_order_id)
            .order_by(Delivery.created_at.asc())
        ).all()
        return [_delivery_to_dict(r) for r in rows]

    def add_shipment(
        self,
        shipment_number: str,
        delivery_id: UUID,
        recorded_at: datetime,
        **fields: Any,
    ) -> dict:
        row = self._insert_if_absent(
            Shipment,
            {"shipment_number": shipment_number},
            delivery_id=delivery_id,
            recorded_at=recorded_at,
            **fields,
        )
        return _shipment_to_dict(row)

    def list_shipments_for_delivery(self, delivery_id: UUID) -> list[dict]:
        rows = self._session.scalars(
            select(Shipment).where(Shipment.delivery_id == delivery_id).order_by(Shipment.recorded_at.asc())
        ).all()
        return [_shipment_to_dict(r) for r in rows]

    def list_shipments_for_purchase_order(self, purchase_order_id: UUID) -> list[dict]:
        """Full shipment history for a PO, oldest first, across all of its
        deliveries."""
        rows = self._session.scalars(
            select(Shipment)
            .join(Delivery, Delivery.id == Shipment.delivery_id)
            .where(Delivery.purchase_order_id == purchase_order_id)
            .order_by(Shipment.recorded_at.asc())
        ).all()
        return [_shipment_to_dict(r) for r in rows]

    def get_latest_shipment_for_purchase_order_not_after(
        self, purchase_order_id: UUID, as_of_date: date
    ) -> dict | None:
        row = self._session.scalars(
            select(Shipment)
            .join(Delivery, Delivery.id == Shipment.delivery_id)
            .where(
                Delivery.purchase_order_id == purchase_order_id,
                Shipment.recorded_at <= _end_of_day(as_of_date),
            )
            .order_by(Shipment.recorded_at.desc())
            .limit(1)
        ).first()
        return _shipment_to_dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # Production
    # ------------------------------------------------------------------

    def add_production_order(self, production_order_number: str, **fields: Any) -> dict:
        row = self._insert_if_absent(
            ProductionOrder, {"production_order_number": production_order_number}, **fields
        )
        return _production_order_to_dict(row)

    def add_production_schedule(
        self,
        material_id: UUID,
        plant_id: UUID,
        status: str,
        status_at: datetime,
        production_order_id: UUID | None = None,
        scheduled_quantity: float | None = None,
        scheduled_start_at: datetime | None = None,
        scheduled_end_at: datetime | None = None,
    ) -> dict:
        row = self._insert_if_absent(
            ProductionSchedule,
            {"material_id": material_id, "plant_id": plant_id, "status_at": status_at},
            status=status,
            production_order_id=production_order_id,
            scheduled_quantity=scheduled_quantity,
            scheduled_start_at=scheduled_start_at,
            scheduled_end_at=scheduled_end_at,
        )
        return _production_schedule_to_dict(row)

    def list_production_schedule_for_material_plant(self, material_id: UUID, plant_id: UUID) -> list[dict]:
        """Not purchase_order-scoped: a production line can serve multiple
        orders that share the same (material_id, plant_id) -- see
        app/models/common/production.py's ProductionSchedule docstring."""
        rows = self._session.scalars(
            select(ProductionSchedule)
            .where(ProductionSchedule.material_id == material_id, ProductionSchedule.plant_id == plant_id)
            .order_by(ProductionSchedule.status_at.asc(), ProductionSchedule.id.asc())
        ).all()
        return [_production_schedule_to_dict(r) for r in rows]

    def get_latest_production_schedule_not_after(
        self, material_id: UUID, plant_id: UUID, as_of_date: date
    ) -> dict | None:
        # Tiebreaker on id: two rows can share the same status_at (a plant/
        # material can serve more than one order, and independently
        # authored demo/mock scenarios can write more than one status for
        # the same material/plant/day). status_at alone doesn't
        # disambiguate a tie; id does, deterministically.
        row = self._session.scalars(
            select(ProductionSchedule)
            .where(
                ProductionSchedule.material_id == material_id,
                ProductionSchedule.plant_id == plant_id,
                ProductionSchedule.status_at <= _end_of_day(as_of_date),
            )
            .order_by(ProductionSchedule.status_at.desc(), ProductionSchedule.id.desc())
            .limit(1)
        ).first()
        return _production_schedule_to_dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # Demand exception
    # ------------------------------------------------------------------

    def add_demand_exception(
        self, exception_id: str, purchase_order_line_id: UUID, flagged_date: date
    ) -> dict:
        row = self._insert_if_absent(
            DemandException,
            {"exception_id": exception_id},
            purchase_order_line_id=purchase_order_line_id,
            flagged_date=flagged_date,
            resolved=False,
        )
        return _demand_exception_to_dict(row)

    def list_demand_exceptions_for_line(self, purchase_order_line_id: UUID) -> list[dict]:
        rows = self._session.scalars(
            select(DemandException)
            .where(DemandException.purchase_order_line_id == purchase_order_line_id)
            .order_by(DemandException.flagged_date.asc())
        ).all()
        return [_demand_exception_to_dict(r) for r in rows]

    def has_open_demand_exception_not_after(self, purchase_order_line_id: UUID, as_of_date: date) -> bool:
        row = self._session.scalars(
            select(DemandException)
            .where(
                DemandException.purchase_order_line_id == purchase_order_line_id,
                DemandException.resolved.is_(False),
                DemandException.flagged_date <= as_of_date,
            )
            .limit(1)
        ).first()
        return row is not None

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def truncate_all(self) -> None:
        """Deletes every fulfillment-fact row, in FK-safe child-before-
        parent order. Must run before PurchaseOrderRepository.truncate_all()
        clears purchase_order/purchase_order_line."""
        self._session.execute(delete(OrderConfirmationLine))
        self._session.execute(delete(OrderConfirmation))
        self._session.execute(delete(Shipment))
        self._session.execute(delete(DeliveryLine))
        self._session.execute(delete(Delivery))
        self._session.execute(delete(ProductionSchedule))
        self._session.execute(delete(ProductionOrder))
        self._session.execute(delete(DemandException))
        self._session.flush()
