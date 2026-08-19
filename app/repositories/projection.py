"""Repository for projected-fine rows -- the daily snapshot history a projection trend is plotted from."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProjectedFine
from app.services.fine_projection import ProjectionResult


def _to_dict(row: ProjectedFine) -> dict:
    return {
        "order_id": row.order_id,
        "rule_id": row.rule_id,
        "projection_date": row.projection_date,
        "violation_type": row.violation_type,
        "failure_probability": float(row.failure_probability),
        "projected_fine_amount": float(row.projected_fine_amount),
        "days_to_delivery": row.days_to_delivery,
        "projection_status": row.projection_status,
    }


class ProjectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_result(self, result: ProjectionResult) -> None:
        for v in result.violations:
            existing = self._session.scalars(
                select(ProjectedFine).where(
                    ProjectedFine.order_id == result.order_id,
                    ProjectedFine.rule_id == v.rule_id,
                    ProjectedFine.projection_date == result.projection_date,
                )
            ).first()

            if existing is not None:
                existing.violation_type = v.violation_type
                existing.failure_probability = v.probability
                existing.projected_fine_amount = v.expected_fine
                existing.days_to_delivery = result.days_to_delivery
                existing.projection_status = "OPEN"
            else:
                self._session.add(
                    ProjectedFine(
                        order_id=result.order_id,
                        rule_id=v.rule_id,
                        projection_date=result.projection_date,
                        violation_type=v.violation_type,
                        failure_probability=v.probability,
                        projected_fine_amount=v.expected_fine,
                        days_to_delivery=result.days_to_delivery,
                        projection_status="OPEN",
                    )
                )
        self._session.flush()

    def list_history(self, order_id: str) -> list[dict]:
        rows = self._session.scalars(
            select(ProjectedFine)
            .where(ProjectedFine.order_id == order_id)
            .order_by(
                ProjectedFine.projection_date.asc(),
                ProjectedFine.rule_id.asc(),
            )
        ).all()

        return [_to_dict(r) for r in rows]

    def get_history(self, order_id: str) -> list[dict]:
        """Deprecated alias for list_history -- kept only because
        app/services/fine_summary.py is off-limits to edit in this pass."""
        return self.list_history(order_id)

    def get_latest(self, order_id: str) -> dict | None:
        history = self.list_history(order_id)
        if not history:
            return None

        latest_date = max(h["projection_date"] for h in history)
        rows = [h for h in history if h["projection_date"] == latest_date]
        total = sum(r["projected_fine_amount"] for r in rows)

        return {
            "order_id": order_id,
            "projection_date": latest_date,
            "total_expected_fine": round(total, 2),
            "violations": rows,
        }
