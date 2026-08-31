"""Repository for per-PO mitigation cause/cost assumptions
(`penalties.mitigation_input`) and persisted, ranked mitigation options
(`penalties.mitigation_option`). Was
`app/repositories/fine_mitigation/mitigation.py`
(`MitigationRepository`/`MitigationResultRepository`).

**Naming collision, inherited from the approved Phase 1 plan, not
introduced here:** the ORM model `app.models.penalties.mitigation.MitigationOption`
and the pure-engine dataclass `app.services.fine_mitigation.types.MitigationOption`
now share the same class name (the plan explicitly unifies on it for the
ORM class, which used to be called `MitigationResult` to avoid exactly
this clash) -- both are imported below, aliased to keep them apart.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import MitigationInput
from app.models import MitigationOption as MitigationOptionModel
from app.services.penalties.mitigation.types import MitigationInputs, ShortageCause
from app.services.penalties.mitigation.types import MitigationOption as MitigationOptionValue


def _row_to_inputs(row: MitigationInput, purchase_order_id: UUID) -> MitigationInputs:
    return MitigationInputs(
        order_id=str(purchase_order_id),
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


class MitigationInputRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _get_row(self, purchase_order_id: UUID) -> MitigationInput | None:
        return self._session.scalars(
            select(MitigationInput).where(MitigationInput.purchase_order_id == purchase_order_id)
        ).first()

    def list_purchase_order_ids(self) -> list[UUID]:
        """PO ids that already have a mitigation-input row, for idempotent seeding."""
        return list(self._session.scalars(select(MitigationInput.purchase_order_id)).all())

    def get_inputs(self, purchase_order_id: UUID) -> MitigationInputs:
        """Never raises: a PO with no row simply has all-default
        (unknown/unconfirmed) inputs."""
        row = self._get_row(purchase_order_id)
        if row is None:
            return MitigationInputs(order_id=str(purchase_order_id))
        return _row_to_inputs(row, purchase_order_id)

    def upsert_inputs(self, purchase_order_id: UUID, **fields: Any) -> None:
        """Idempotent: insert if the PO has no row yet, else update in place."""
        row = self._get_row(purchase_order_id)
        if row is None:
            self._session.add(MitigationInput(purchase_order_id=purchase_order_id, **fields))
        else:
            for key, value in fields.items():
                setattr(row, key, value)
        self._session.flush()

    def truncate_all(self) -> None:
        """Deletes every mitigation_input row, plus mitigation_option
        (otherwise owned by MitigationOptionRepository) -- both FK to
        purchase_order, so both must be cleared before
        PurchaseOrderRepository.truncate_all() clears purchase_order
        itself."""
        self._session.execute(delete(MitigationOptionModel))
        self._session.execute(delete(MitigationInput))
        self._session.flush()


def _option_to_dict(row: MitigationOptionModel) -> dict:
    return {
        "id": row.id,
        "purchase_order_id": row.purchase_order_id,
        "projection_date": row.projection_date,
        "action": row.action,
        "projected_penalty_after": float(row.projected_penalty_after),
        "action_cost": float(row.action_cost),
        "net_saving": float(row.net_saving),
        "risk_level": row.risk_level,
        "confidence": row.confidence,
        "rationale": row.rationale,
    }


class MitigationOptionRepository:
    """Repository for mitigation_option -- persisted, ranked output of
    app/services/fine_mitigation/engine.py::MitigationEngine.evaluate.
    Was `MitigationResultRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    def _find(
        self, purchase_order_id: UUID, projection_date: date, action: str
    ) -> MitigationOptionModel | None:
        return self._session.scalars(
            select(MitigationOptionModel).where(
                MitigationOptionModel.purchase_order_id == purchase_order_id,
                MitigationOptionModel.projection_date == projection_date,
                MitigationOptionModel.action == action,
            )
        ).first()

    def save_results(
        self,
        purchase_order_id: UUID,
        projection_date: date,
        options: list[MitigationOptionValue],
    ) -> list[dict]:
        """Upsert one row per option, keyed on (purchase_order_id,
        projection_date, action). Never deletes: an action that is no
        longer structurally eligible on a later re-run simply stops being
        written, its previous row is left in place as history (same
        append-only posture as every other fact table)."""
        rows: list[dict] = []
        for option in options:
            existing = self._find(purchase_order_id, projection_date, option.action)
            if existing is not None:
                existing.projected_penalty_after = option.projected_penalty_after
                existing.action_cost = option.action_cost
                existing.net_saving = option.net_saving
                existing.risk_level = option.risk_level
                existing.confidence = option.confidence
                existing.rationale = option.rationale
                self._session.flush()
                rows.append(_option_to_dict(existing))
                continue

            row = MitigationOptionModel(
                purchase_order_id=purchase_order_id,
                projection_date=projection_date,
                action=option.action,
                projected_penalty_after=option.projected_penalty_after,
                action_cost=option.action_cost,
                net_saving=option.net_saving,
                risk_level=option.risk_level,
                confidence=option.confidence,
                rationale=option.rationale,
            )
            self._session.add(row)
            self._session.flush()
            rows.append(_option_to_dict(row))

        return rows

    def get_by_id(self, mitigation_option_id: UUID) -> dict | None:
        """Fetch one `mitigation_option` row by its own surrogate id.

        Phase 7a addition (flagged -- repositories were nominally out of
        scope for that phase): `GET /penalty-mitigations/{mitigation_id}`
        (approved plan §5) has no other way to resolve a single option row;
        every other method here is keyed by `(purchase_order_id,
        projection_date)`, not by this table's own `id`. Purely additive,
        zero behavior change to any existing method."""
        row = self._session.get(MitigationOptionModel, mitigation_option_id)
        return _option_to_dict(row) if row is not None else None

    def list_for_date(self, purchase_order_id: UUID, projection_date: date) -> list[dict]:
        """Ranked (net_saving descending) options for one exact day."""
        rows = self._session.scalars(
            select(MitigationOptionModel).where(
                MitigationOptionModel.purchase_order_id == purchase_order_id,
                MitigationOptionModel.projection_date == projection_date,
            )
        ).all()
        return sorted((_option_to_dict(r) for r in rows), key=lambda r: r["net_saving"], reverse=True)

    def _latest_date_not_after(self, purchase_order_id: UUID, not_after: date | None = None) -> date | None:
        stmt = select(func.max(MitigationOptionModel.projection_date)).where(
            MitigationOptionModel.purchase_order_id == purchase_order_id,
        )
        if not_after is not None:
            stmt = stmt.where(MitigationOptionModel.projection_date <= not_after)
        return self._session.scalar(stmt)

    def get_latest(self, purchase_order_id: UUID) -> list[dict]:
        """Ranked options for the most recent projection_date on record."""
        latest_date = self._latest_date_not_after(purchase_order_id)
        if latest_date is None:
            return []
        return self.list_for_date(purchase_order_id, latest_date)

    def get_latest_not_after(self, purchase_order_id: UUID, as_of_date: date) -> list[dict]:
        """Ranked options for the most recent projection_date <= as_of_date.

        Same nearest-prior-date reasoning as
        PenaltySummaryRepository.get_latest_ready_not_after.
        """
        latest_date = self._latest_date_not_after(purchase_order_id, not_after=as_of_date)
        if latest_date is None:
            return []
        return self.list_for_date(purchase_order_id, latest_date)

    def earliest_date(self, purchase_order_id: UUID) -> date | None:
        return self._session.scalar(
            select(func.min(MitigationOptionModel.projection_date)).where(
                MitigationOptionModel.purchase_order_id == purchase_order_id
            )
        )
