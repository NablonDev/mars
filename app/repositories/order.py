"""Repository for orders and their historized operational facts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import OrderAlreadyExistsError, OrderNotFoundError
from app.models import (
    ActualFine,
    Carrier,
    DemandException,
    Order,
    OrderConfirmation,
    ProductionSchedule,
    ProjectedFine,
    ProjectionSummary,
    Shipment,
)
from app.services.fine_projection import AppointmentStatus, OrderSnapshot, ProductionStatus


def describe_no_open_orders(counts: dict[str, int]) -> str | None:
    """Explain a zero-OPEN-order batch, or None when there is nothing to
    explain (no orders at all, or some are OPEN after all).

    A batch that enqueues nothing because every order is DELIVERED looks
    identical to a broken one in the logs, so both callers say which it is.
    """
    if counts.get("OPEN") or not counts:
        return None

    breakdown = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    return (
        f"No OPEN orders to enqueue, but {sum(counts.values())} order(s) exist ({breakdown}). "
        "Nothing will run until an order is OPEN -- re-seed, or reopen the existing orders."
    )


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time())


def _order_to_dict(order: Order) -> dict:
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


def _confirmation_to_dict(row: OrderConfirmation) -> dict:
    return {
        "confirmation_id": row.confirmation_id,
        "order_id": row.order_id,
        "confirmed_qty": row.confirmed_qty,
        "confirmation_date": row.confirmation_date,
        "cut_reason_code": row.cut_reason_code,
    }


def _production_status_to_dict(row: ProductionSchedule) -> dict:
    return {
        "production_id": row.production_id,
        "sku_id": row.sku_id,
        "location_id": row.location_id,
        "status": row.status,
        "status_date": row.status_date,
    }


def _shipment_to_dict(row: Shipment) -> dict:
    return {
        "shipment_id": row.shipment_id,
        "order_id": row.order_id,
        "carrier_id": row.carrier_id,
        "expected_ship_date": row.expected_ship_date,
        "actual_ship_date": row.actual_ship_date,
        "appointment_status": row.appointment_status,
        "expected_transit_days": row.expected_transit_days,
        "recorded_at": row.recorded_at,
    }


def _demand_exception_to_dict(row: DemandException) -> dict:
    return {
        "exception_id": row.exception_id,
        "order_id": row.order_id,
        "flagged_date": row.flagged_date,
        "resolved": row.resolved,
    }


def _actual_fine_to_dict(row: ActualFine) -> dict:
    return {
        "actual_fine_id": row.actual_fine_id,
        "order_id": row.order_id,
        "retailer_id": row.retailer_id,
        "violation_type": row.violation_type,
        "actual_fine_amount": float(row.actual_fine_amount),
        "invoice_or_deduction_date": row.invoice_or_deduction_date,
        "dispute_status": row.dispute_status,
    }


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _get_order_row(self, order_id: str) -> Order | None:
        """Look up an order by its business key."""
        return self._session.scalars(select(Order).where(Order.order_id == order_id)).first()

    def _get_carrier_row(self, carrier_id: str) -> Carrier | None:
        return self._session.scalars(select(Carrier).where(Carrier.carrier_id == carrier_id)).first()

    def _insert_if_absent(
        self,
        model: type,
        key_column: Any,
        key_value: Any,
        **fields: Any,
    ) -> None:
        """Insert a fact only when its natural key does not already exist."""
        existing = self._session.scalars(select(model).where(key_column == key_value)).first()
        if existing is not None:
            return

        self._session.add(model(**{key_column.key: key_value}, **fields))
        self._session.flush()

    def create_order(self, **fields) -> None:
        if self._get_order_row(fields["order_id"]) is not None:
            raise OrderAlreadyExistsError(fields["order_id"])

        self._session.add(Order(**fields))
        self._session.flush()

    def get_order(self, order_id: str) -> dict | None:
        order = self._get_order_row(order_id)
        return _order_to_dict(order) if order else None

    def require_order(self, order_id: str) -> dict:
        order = self.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        return order

    def list_orders(self, order_status: str | None = None) -> list[dict]:
        stmt = select(Order)
        if order_status:
            stmt = stmt.where(Order.order_status == order_status)

        rows = self._session.scalars(stmt).all()
        return [_order_to_dict(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        """Order counts keyed by `order_status`, for diagnostics."""
        rows = self._session.execute(
            select(Order.order_status, func.count()).group_by(Order.order_status)
        ).all()
        return {status: count for status, count in rows}

    def set_order_status(self, order_id: str, order_status: str) -> None:
        order = self._get_order_row(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        order.order_status = order_status
        self._session.flush()

    def add_confirmation(
        self,
        order_id: str,
        confirmation_id: str,
        confirmed_qty: int,
        confirmation_date: datetime,
        cut_reason_code: str | None = None,
    ) -> None:
        self._insert_if_absent(
            OrderConfirmation,
            OrderConfirmation.confirmation_id,
            confirmation_id,
            order_id=order_id,
            confirmed_qty=confirmed_qty,
            confirmation_date=confirmation_date,
            cut_reason_code=cut_reason_code,
        )

    def add_production_status(
        self,
        production_id: str,
        sku_id: str,
        location_id: str,
        status: str,
        status_date: datetime,
    ) -> None:
        self._insert_if_absent(
            ProductionSchedule,
            ProductionSchedule.production_id,
            production_id,
            sku_id=sku_id,
            location_id=location_id,
            status=status,
            status_date=status_date,
        )

    def record_shipment_event(
        self,
        order_id: str,
        carrier_id: str | None,
        expected_ship_date: date | None,
        actual_ship_date: date | None,
        appointment_status: str,
        expected_transit_days: int,
        recorded_at: datetime,
    ) -> None:
        shipment_id = f"SHIP-{order_id}-{recorded_at.strftime('%Y%m%d%H%M%S')}"
        self._insert_if_absent(
            Shipment,
            Shipment.shipment_id,
            shipment_id,
            order_id=order_id,
            carrier_id=carrier_id,
            expected_ship_date=expected_ship_date,
            actual_ship_date=actual_ship_date,
            appointment_status=appointment_status,
            expected_transit_days=expected_transit_days,
            recorded_at=recorded_at,
        )

    def add_demand_exception(self, exception_id: str, order_id: str, flagged_date: date) -> None:
        self._insert_if_absent(
            DemandException,
            DemandException.exception_id,
            exception_id,
            order_id=order_id,
            flagged_date=flagged_date,
            resolved=False,
        )

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
        # not "whatever the current row says today."
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
        """Full history, oldest first -- unlike build_snapshot, which only needs the latest-as-of-a-date row."""
        rows = self._session.scalars(
            select(OrderConfirmation)
            .where(OrderConfirmation.order_id == order_id)
            .order_by(OrderConfirmation.confirmation_date.asc())
        ).all()

        return [_confirmation_to_dict(r) for r in rows]

    def list_shipments(self, order_id: str) -> list[dict]:
        rows = self._session.scalars(
            select(Shipment).where(Shipment.order_id == order_id).order_by(Shipment.recorded_at.asc())
        ).all()

        return [_shipment_to_dict(r) for r in rows]

    def list_demand_exceptions(self, order_id: str) -> list[dict]:
        rows = self._session.scalars(
            select(DemandException)
            .where(DemandException.order_id == order_id)
            .order_by(DemandException.flagged_date.asc())
        ).all()

        return [_demand_exception_to_dict(r) for r in rows]

    def list_production_status_history(self, order_id: str) -> list[dict]:
        """Not order_id-scoped: a production line can serve multiple orders."""
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

        return [_production_status_to_dict(r) for r in rows]

    def list_actual_fines(self, order_id: str) -> list[dict]:
        rows = self._session.scalars(select(ActualFine).where(ActualFine.order_id == order_id)).all()
        return [_actual_fine_to_dict(r) for r in rows]

    def list_open_orders_for_sku_location(
        self,
        sku_id: str,
        location_id: str,
        exclude_order_id: str,
    ) -> list[str]:
        """Other OPEN orders drawing on the same production line"""
        rows = self._session.scalars(
            select(Order.order_id).where(
                Order.sku_id == sku_id,
                Order.ship_from_location_id == location_id,
                Order.order_status == "OPEN",
                Order.order_id != exclude_order_id,
            )
        ).all()
        return list(rows)

    def truncate_all(self) -> None:
        """Deletes every sales_order row and every other table that
        FK-references it (directly, or via sku/location for
        production_schedule), in FK-safe child-before-parent order.

        Caller must clear mitigation_input and mitigation_option first --
        both also FK to sales_order. See MitigationRepository.truncate_all
        and FineSeedingService._truncate_seeded_tables for the full order.
        """
        self._session.execute(delete(OrderConfirmation))
        self._session.execute(delete(ProductionSchedule))
        self._session.execute(delete(Shipment))
        self._session.execute(delete(DemandException))
        self._session.execute(delete(ActualFine))
        self._session.execute(delete(ProjectedFine))
        self._session.execute(delete(ProjectionSummary))
        self._session.execute(delete(Order))
        self._session.flush()
