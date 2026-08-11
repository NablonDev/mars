"""
Repository for orders and their daily facts (confirmations, production
status, shipment events, demand exceptions, actual fines). `build_snapshot`
is the one method the projection service actually needs -- it assembles
the engine's `OrderSnapshot` purely from historized facts, no
current-state shortcuts.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import OrderNotFoundError
from app.engine import AppointmentStatus, OrderSnapshot, ProductionStatus
from app.models import (
    ActualFine,
    Carrier,
    DemandException,
    OrderConfirmation,
    OrderORM,
    ProductionSchedule,
    Shipment,
)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time())


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _get_order_row(self, order_id: str) -> OrderORM | None:
        """`order_id` is a unique business key, not the table's surrogate
        `id` primary key -- so this is a `select().where()`, not `.get()`."""
        return self._session.scalars(select(OrderORM).where(OrderORM.order_id == order_id)).first()

    def _get_carrier_row(self, carrier_id: str) -> Carrier | None:
        return self._session.scalars(select(Carrier).where(Carrier.carrier_id == carrier_id)).first()

    def create_order(self, **fields) -> None:
        self._session.add(OrderORM(**fields))
        self._session.flush()

    def get_order(self, order_id: str) -> dict | None:
        order = self._get_order_row(order_id)
        if order is None:
            return None
        return {
            "order_id": order.order_id,
            "retailer_id": order.retailer_id,
            "sku_id": order.sku_id,
            "ship_from_location_id": order.ship_from_location_id,
            "order_qty": order.order_qty,
            "unit_price": float(order.unit_price),
            "order_date": order.order_date,
            "requested_delivery_date": order.requested_delivery_date,
            "required_ship_date": order.required_ship_date,
            "order_status": order.order_status,
            "carrier_id": order.carrier_id,
        }

    def list_orders(self, order_status: str | None = None) -> list[dict]:
        stmt = select(OrderORM)
        if order_status:
            stmt = stmt.where(OrderORM.order_status == order_status)
        rows = self._session.scalars(stmt).all()
        return [self.get_order(r.order_id) for r in rows]

    def set_order_status(self, order_id: str, order_status: str) -> None:
        order = self._get_order_row(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)
        order.order_status = order_status
        self._session.flush()

    def add_confirmation(
        self, order_id, confirmation_id, confirmed_qty, confirmation_date, cut_reason_code=None
    ) -> None:
        # Idempotent on confirmation_id: a caller (e.g. SeedingService.simulate_daily_run,
        # or a retried HTTP POST) re-submitting the same fact is a no-op, not a
        # crash. confirmation_id is a deterministic natural key here
        # ("CONF-{order_id}-{date}"), so seeing it twice means "already recorded
        # this," not "two different facts collided."
        existing = self._session.scalars(
            select(OrderConfirmation).where(OrderConfirmation.confirmation_id == confirmation_id)
        ).first()
        if existing is not None:
            return
        self._session.add(
            OrderConfirmation(
                confirmation_id=confirmation_id,
                order_id=order_id,
                confirmed_qty=confirmed_qty,
                confirmation_date=confirmation_date,
                cut_reason_code=cut_reason_code,
            )
        )
        self._session.flush()

    def add_production_status(self, production_id, sku_id, location_id, status, status_date) -> None:
        existing = self._session.scalars(
            select(ProductionSchedule).where(ProductionSchedule.production_id == production_id)
        ).first()
        if existing is not None:
            return
        self._session.add(
            ProductionSchedule(
                production_id=production_id,
                sku_id=sku_id,
                location_id=location_id,
                status=status,
                status_date=status_date,
            )
        )
        self._session.flush()

    def record_shipment_event(
        self,
        order_id,
        carrier_id,
        expected_ship_date,
        actual_ship_date,
        appointment_status,
        expected_transit_days,
        recorded_at,
    ) -> None:
        shipment_id = f"SHIP-{order_id}-{recorded_at.strftime('%Y%m%d%H%M%S')}"
        existing = self._session.scalars(select(Shipment).where(Shipment.shipment_id == shipment_id)).first()
        if existing is not None:
            return
        self._session.add(
            Shipment(
                shipment_id=shipment_id,
                order_id=order_id,
                carrier_id=carrier_id,
                expected_ship_date=expected_ship_date,
                actual_ship_date=actual_ship_date,
                appointment_status=appointment_status,
                expected_transit_days=expected_transit_days,
                recorded_at=recorded_at,
            )
        )
        self._session.flush()

    def add_demand_exception(self, exception_id, order_id, flagged_date) -> None:
        existing = self._session.scalars(
            select(DemandException).where(DemandException.exception_id == exception_id)
        ).first()
        if existing is not None:
            return
        self._session.add(
            DemandException(
                exception_id=exception_id,
                order_id=order_id,
                flagged_date=flagged_date,
                resolved=False,
            )
        )
        self._session.flush()

    def build_snapshot(self, order_id: str, projection_date: date) -> OrderSnapshot:
        order = self._get_order_row(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        confirmation = self._session.scalars(
            select(OrderConfirmation)
            .where(
                OrderConfirmation.order_id == order_id,
                OrderConfirmation.confirmation_date <= _end_of_day(projection_date),
            )
            .order_by(OrderConfirmation.confirmation_date.desc())
            .limit(1)
        ).first()
        confirmed_qty = confirmation.confirmed_qty if confirmation else order.order_qty

        # Tiebreaker on production_id: a plant/SKU can serve more than one
        # order, and demo/mock scenarios authored independently can (and,
        # in the four canonical examples, sometimes do) write more than
        # one status for the same sku/location/day. status_date alone
        # doesn't disambiguate a tie; production_id does, deterministically.
        production = self._session.scalars(
            select(ProductionSchedule)
            .where(
                ProductionSchedule.sku_id == order.sku_id,
                ProductionSchedule.location_id == order.ship_from_location_id,
                ProductionSchedule.status_date <= _end_of_day(projection_date),
            )
            .order_by(ProductionSchedule.status_date.desc(), ProductionSchedule.production_id.desc())
            .limit(1)
        ).first()
        production_status = ProductionStatus(production.status) if production else ProductionStatus.ON_TRACK

        exception = self._session.scalars(
            select(DemandException)
            .where(
                DemandException.order_id == order_id,
                DemandException.resolved.is_(False),
                DemandException.flagged_date <= projection_date,
            )
            .limit(1)
        ).first()

        # Historized: latest shipment fact recorded on or before projection_date,
        # not "whatever the current row says today." See docs/FINE_ENGINE.md.
        shipment = self._session.scalars(
            select(Shipment)
            .where(
                Shipment.order_id == order_id,
                Shipment.recorded_at <= _end_of_day(projection_date),
            )
            .order_by(Shipment.recorded_at.desc())
            .limit(1)
        ).first()

        carrier_reliability = 90.0
        appointment_status = AppointmentStatus.SCHEDULED
        actual_ship_date = None
        expected_ship_date = None
        expected_transit_days = 2
        if shipment is not None:
            appointment_status = AppointmentStatus(shipment.appointment_status)
            actual_ship_date = shipment.actual_ship_date
            expected_ship_date = shipment.expected_ship_date
            expected_transit_days = shipment.expected_transit_days
            carrier_id = shipment.carrier_id or order.carrier_id
            if carrier_id:
                carrier = self._get_carrier_row(carrier_id)
                if carrier:
                    carrier_reliability = float(carrier.historical_reliability_score)
        elif order.carrier_id:
            carrier = self._get_carrier_row(order.carrier_id)
            if carrier:
                carrier_reliability = float(carrier.historical_reliability_score)

        return OrderSnapshot(
            order_id=order.order_id,
            projection_date=projection_date,
            order_qty=order.order_qty,
            unit_price=float(order.unit_price),
            requested_delivery_date=order.requested_delivery_date,
            required_ship_date=order.required_ship_date,
            confirmed_qty=confirmed_qty,
            production_status=production_status,
            demand_exception_flagged=exception is not None,
            expected_ship_date=expected_ship_date,
            actual_ship_date=actual_ship_date,
            appointment_status=appointment_status,
            carrier_reliability_score=carrier_reliability,
            expected_transit_days=expected_transit_days,
        )

    def add_actual_fine(
        self,
        actual_fine_id,
        order_id,
        retailer_id,
        violation_type,
        actual_fine_amount,
        invoice_or_deduction_date,
        dispute_status="NONE",
    ) -> None:
        self._session.add(
            ActualFine(
                actual_fine_id=actual_fine_id,
                order_id=order_id,
                retailer_id=retailer_id,
                violation_type=violation_type,
                actual_fine_amount=actual_fine_amount,
                invoice_or_deduction_date=invoice_or_deduction_date,
                dispute_status=dispute_status,
            )
        )
        self._session.flush()

    def list_confirmations(self, order_id: str) -> list[dict]:
        """Full `fact_order_confirmation` history for one order, oldest
        first -- unlike `build_snapshot`, which only ever needs the single
        latest-as-of-a-date row. The explanation layer needs the whole
        timeline to narrate what changed and when."""
        rows = self._session.scalars(
            select(OrderConfirmation)
            .where(OrderConfirmation.order_id == order_id)
            .order_by(OrderConfirmation.confirmation_date.asc())
        ).all()
        return [
            {
                "confirmation_id": r.confirmation_id,
                "order_id": r.order_id,
                "confirmed_qty": r.confirmed_qty,
                "confirmation_date": r.confirmation_date,
                "cut_reason_code": r.cut_reason_code,
            }
            for r in rows
        ]

    def list_shipments(self, order_id: str) -> list[dict]:
        """Full `fact_shipment` history for one order, oldest first."""
        rows = self._session.scalars(
            select(Shipment).where(Shipment.order_id == order_id).order_by(Shipment.recorded_at.asc())
        ).all()
        return [
            {
                "shipment_id": r.shipment_id,
                "order_id": r.order_id,
                "carrier_id": r.carrier_id,
                "expected_ship_date": r.expected_ship_date,
                "actual_ship_date": r.actual_ship_date,
                "appointment_status": r.appointment_status,
                "expected_transit_days": r.expected_transit_days,
                "recorded_at": r.recorded_at,
            }
            for r in rows
        ]

    def list_demand_exceptions(self, order_id: str) -> list[dict]:
        """Full `fact_demand_exception` history for one order, oldest first."""
        rows = self._session.scalars(
            select(DemandException)
            .where(DemandException.order_id == order_id)
            .order_by(DemandException.flagged_date.asc())
        ).all()
        return [
            {
                "exception_id": r.exception_id,
                "order_id": r.order_id,
                "flagged_date": r.flagged_date,
                "resolved": r.resolved,
            }
            for r in rows
        ]

    def list_production_status_history(self, order_id: str) -> list[dict]:
        """Full `fact_production_schedule` history for the (sku_id,
        location_id) the order draws on -- not filtered to `order_id`,
        because that table has no `order_id` column: a production line
        serves whichever orders share it, and `build_snapshot` already
        treats it that way. Returning every row for the shared line
        (not just "this order's own") is deliberate -- see the
        AMZ-778501/AMZ-780112 shared-plant caveat in
        docs/FINE_ENGINE.md: the explanation layer needs the honest
        raw data to flag that caveat, not a filtered view that hides it."""
        order = self._get_order_row(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)
        rows = self._session.scalars(
            select(ProductionSchedule)
            .where(
                ProductionSchedule.sku_id == order.sku_id,
                ProductionSchedule.location_id == order.ship_from_location_id,
            )
            .order_by(ProductionSchedule.status_date.asc(), ProductionSchedule.production_id.asc())
        ).all()
        return [
            {
                "production_id": r.production_id,
                "sku_id": r.sku_id,
                "location_id": r.location_id,
                "status": r.status,
                "status_date": r.status_date,
            }
            for r in rows
        ]

    def list_actual_fines(self, order_id: str) -> list[dict]:
        rows = self._session.scalars(select(ActualFine).where(ActualFine.order_id == order_id)).all()
        return [
            {
                "actual_fine_id": r.actual_fine_id,
                "order_id": r.order_id,
                "retailer_id": r.retailer_id,
                "violation_type": r.violation_type,
                "actual_fine_amount": float(r.actual_fine_amount),
                "invoice_or_deduction_date": r.invoice_or_deduction_date,
                "dispute_status": r.dispute_status,
            }
            for r in rows
        ]
