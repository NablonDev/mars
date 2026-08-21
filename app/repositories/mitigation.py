"""Repository for per-order mitigation cause/cost assumptions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MitigationInput
from app.services.fine_mitigation.models import MitigationInputs, ShortageCause


def _row_to_inputs(row: MitigationInput) -> MitigationInputs:
    return MitigationInputs(
        order_id=row.order_id,
        shortage_cause=ShortageCause(row.shortage_cause),
        shortage_cause_confirmed=row.shortage_cause_confirmed,
        capacity_boost_cost_per_unit=(
            float(row.capacity_boost_cost_per_unit) if row.capacity_boost_cost_per_unit is not None else None
        ),
        capacity_boost_max_units_per_day=(
            float(row.capacity_boost_max_units_per_day)
            if row.capacity_boost_max_units_per_day is not None
            else None
        ),
        capacity_boost_data_confirmed=row.capacity_boost_data_confirmed,
        express_carrier_cost=(
            float(row.express_carrier_cost) if row.express_carrier_cost is not None else None
        ),
        express_carrier_transit_days=row.express_carrier_transit_days,
        express_carrier_data_confirmed=row.express_carrier_data_confirmed,
        split_shipment_handling_cost=float(row.split_shipment_handling_cost),
    )


class MitigationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _get_row(self, order_id: str) -> MitigationInput | None:
        return self._session.scalars(
            select(MitigationInput).where(MitigationInput.order_id == order_id)
        ).first()

    def list_order_ids(self) -> list[str]:
        """Order ids that already have a mitigation-input row, for idempotent seeding."""
        return list(self._session.scalars(select(MitigationInput.order_id)).all())

    def get_inputs(self, order_id: str) -> MitigationInputs:
        """Never raises: an order with no row simply has all-default (unknown/unconfirmed) inputs."""
        row = self._get_row(order_id)
        if row is None:
            return MitigationInputs(order_id=order_id)
        return _row_to_inputs(row)

    def upsert_inputs(self, order_id: str, **fields: Any) -> None:
        """Idempotent: insert if the order has no row yet, else update in place."""
        row = self._get_row(order_id)
        if row is None:
            self._session.add(MitigationInput(order_id=order_id, **fields))
        else:
            for key, value in fields.items():
                setattr(row, key, value)
        self._session.flush()
