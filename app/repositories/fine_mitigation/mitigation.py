"""Repository for per-order mitigation cause/cost
assumptions (mitigation_input) and persisted, ranked mitigation
options (mitigation_option)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import MitigationInput, MitigationResult, MitigationSummary
from app.services.fine_mitigation.types import MitigationInputs, MitigationOption, ShortageCause


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

    def truncate_all(self) -> None:
        """Deletes every mitigation_input row, plus mitigation_option and
        mitigation_summary (both otherwise owned by
        MitigationResultRepository / FineMitigationSummaryRepository,
        neither wired into FineSeedingService) -- all three FK to
        sales_order, so all three must be cleared before
        OrderRepository.truncate_all() clears sales_order itself. See
        FineSeedingService._truncate_seeded_tables for the full order."""
        self._session.execute(delete(MitigationResult))
        self._session.execute(delete(MitigationSummary))
        self._session.execute(delete(MitigationInput))
        self._session.flush()


def _result_to_dict(row: MitigationResult) -> dict:
    return {
        "order_id": row.order_id,
        "projection_date": row.projection_date,
        "action": row.action,
        "projected_fine_after": float(row.projected_fine_after),
        "action_cost": float(row.action_cost),
        "net_saving": float(row.net_saving),
        "risk_level": row.risk_level,
        "confidence": row.confidence,
        "rationale": row.rationale,
    }


class MitigationResultRepository:
    """Repository for mitigation_option -- persisted, ranked output of
    app/services/fine_mitigation/engine.py::evaluate_mitigation_options."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    def _find(self, order_id: str, projection_date: date, action: str) -> MitigationResult | None:
        return self._session.scalars(
            select(MitigationResult).where(
                MitigationResult.order_id == order_id,
                MitigationResult.projection_date == projection_date,
                MitigationResult.action == action,
            )
        ).first()

    def save_results(
        self,
        order_id: str,
        projection_date: date,
        options: list[MitigationOption],
    ) -> list[dict]:
        """Upsert one row per option, keyed on (order_id, projection_date,
        action) -- mirrors ProjectionRepository.save_result's find-or-insert
        pattern. Never deletes: an action that is no longer structurally
        eligible on a later re-run simply stops being written, its
        previous row is left in place as history (same append-only
        posture as every other fact table)."""
        rows: list[dict] = []
        for option in options:
            existing = self._find(order_id, projection_date, option.action)
            if existing is not None:
                existing.projected_fine_after = option.projected_fine_after
                existing.action_cost = option.action_cost
                existing.net_saving = option.net_saving
                existing.risk_level = option.risk_level
                existing.confidence = option.confidence
                existing.rationale = option.rationale
                self._session.flush()
                rows.append(_result_to_dict(existing))
                continue

            row = MitigationResult(
                order_id=order_id,
                projection_date=projection_date,
                action=option.action,
                projected_fine_after=option.projected_fine_after,
                action_cost=option.action_cost,
                net_saving=option.net_saving,
                risk_level=option.risk_level,
                confidence=option.confidence,
                rationale=option.rationale,
            )
            self._session.add(row)
            self._session.flush()
            rows.append(_result_to_dict(row))

        return rows

    def list_for_date(self, order_id: str, projection_date: date) -> list[dict]:
        """Ranked (net_saving descending) options for one exact day."""
        rows = self._session.scalars(
            select(MitigationResult).where(
                MitigationResult.order_id == order_id,
                MitigationResult.projection_date == projection_date,
            )
        ).all()
        return sorted((_result_to_dict(r) for r in rows), key=lambda r: r["net_saving"], reverse=True)

    def _latest_date_not_after(self, order_id: str, not_after: date | None = None) -> date | None:
        stmt = select(func.max(MitigationResult.projection_date)).where(
            MitigationResult.order_id == order_id,
        )
        if not_after is not None:
            stmt = stmt.where(MitigationResult.projection_date <= not_after)
        return self._session.scalar(stmt)

    def get_latest(self, order_id: str) -> list[dict]:
        """Ranked options for the most recent projection_date on record."""
        latest_date = self._latest_date_not_after(order_id)
        if latest_date is None:
            return []
        return self.list_for_date(order_id, latest_date)

    def get_latest_not_after(self, order_id: str, as_of_date: date) -> list[dict]:
        """Ranked options for the most recent projection_date <= as_of_date.

        Same nearest-prior-date reasoning as
        FineProjectionSummaryRepository.get_latest_ready_not_after.
        """
        latest_date = self._latest_date_not_after(order_id, not_after=as_of_date)
        if latest_date is None:
            return []
        return self.list_for_date(order_id, latest_date)

    def earliest_date(self, order_id: str) -> date | None:
        return self._session.scalar(
            select(func.min(MitigationResult.projection_date)).where(MitigationResult.order_id == order_id)
        )
