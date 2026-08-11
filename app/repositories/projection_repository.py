"""Repository for projected-fine rows -- the daily snapshot history a
projection trend is plotted from."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine import ProjectionResult
from app.models import ProjectedFine


class ProjectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_result(self, result: ProjectionResult) -> None:
        """Upsert -- re-running the same order/date is idempotent. Looks
        up by the (order_id, rule_id, projection_date) unique constraint,
        not the surrogate `id` primary key -- that triple is the actual
        business identity of one projected-fine row."""
        for v in result.violations:
            existing = self._session.scalars(
                select(ProjectedFine).where(
                    ProjectedFine.order_id == result.order_id,
                    ProjectedFine.rule_id == v.rule_id,
                    ProjectedFine.projection_date == result.projection_date,
                )
            ).first()
            if existing:
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

    def get_history(self, order_id: str) -> list[dict]:
        rows = self._session.scalars(
            select(ProjectedFine)
            .where(ProjectedFine.order_id == order_id)
            .order_by(ProjectedFine.projection_date.asc())
        ).all()
        return [
            {
                "order_id": r.order_id,
                "rule_id": r.rule_id,
                "projection_date": r.projection_date,
                "violation_type": r.violation_type,
                "failure_probability": float(r.failure_probability),
                "projected_fine_amount": float(r.projected_fine_amount),
                "days_to_delivery": r.days_to_delivery,
                "projection_status": r.projection_status,
            }
            for r in rows
        ]

    def get_latest(self, order_id: str) -> dict | None:
        history = self.get_history(order_id)
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
