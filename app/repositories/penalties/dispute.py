"""Repository for `penalties.penalty_dispute`.

Modeled after `app.repositories.common.delivery_change_request.
PoDeliveryChangeRequestRepository` (create/get/list/find-active/record-
terminal-state/truncate) -- see `app.models.penalties.dispute.PenaltyDispute`'s
module docstring for the full shape rationale.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PenaltyDispute
from app.models.enums import DisputeStatus

_ACTIVE_STATUSES = (DisputeStatus.OPEN, DisputeStatus.ANALYZED)


def _to_dict(row: PenaltyDispute) -> dict:
    return {
        "id": row.id,
        "dispute_number": row.dispute_number,
        "actual_penalty_id": row.actual_penalty_id,
        "purchase_order_id": row.purchase_order_id,
        "rule_id": row.rule_id,
        "reason_code": row.reason_code,
        "claimed_amount": float(row.claimed_amount),
        "computed_amount": float(row.computed_amount) if row.computed_amount is not None else None,
        "delta_amount": float(row.delta_amount) if row.delta_amount is not None else None,
        "verdict": row.verdict,
        "dispute_status": row.dispute_status,
        "analysis_breakdown": row.analysis_breakdown,
        "analyzed_at": row.analyzed_at,
        "resolved_at": row.resolved_at,
        "resolved_by": row.resolved_by,
        "override_verdict": row.override_verdict,
        "override_reason": row.override_reason,
        "notes": row.notes,
        "created_at": row.created_at,
    }


class PenaltyDisputeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        actual_penalty_id: UUID,
        purchase_order_id: UUID,
        reason_code: str,
        claimed_amount: float,
        notes: str | None = None,
    ) -> dict:
        row = PenaltyDispute(
            actual_penalty_id=actual_penalty_id,
            purchase_order_id=purchase_order_id,
            reason_code=reason_code,
            claimed_amount=claimed_amount,
            dispute_status=DisputeStatus.OPEN,
            notes=notes,
        )
        self._session.add(row)
        self._session.flush()
        return _to_dict(row)

    def _get_row(self, dispute_id: UUID) -> PenaltyDispute | None:
        return self._session.scalars(select(PenaltyDispute).where(PenaltyDispute.id == dispute_id)).first()

    def get_by_id(self, dispute_id: UUID) -> dict | None:
        row = self._get_row(dispute_id)
        return _to_dict(row) if row is not None else None

    def get_by_number(self, dispute_number: str) -> dict | None:
        row = self._session.scalars(
            select(PenaltyDispute).where(PenaltyDispute.dispute_number == dispute_number)
        ).first()
        return _to_dict(row) if row is not None else None

    def list_for_actual_penalty(self, actual_penalty_id: UUID) -> list[dict]:
        """Every dispute ever opened against one charge, oldest first -- a
        charge can have more than one dispute cycle over time (see
        `find_active_for_actual_penalty`'s docstring)."""
        rows = self._session.scalars(
            select(PenaltyDispute)
            .where(PenaltyDispute.actual_penalty_id == actual_penalty_id)
            .order_by(PenaltyDispute.created_at.asc())
        ).all()
        return [_to_dict(r) for r in rows]

    def find_active_for_actual_penalty(self, actual_penalty_id: UUID) -> dict | None:
        """Latest OPEN/ANALYZED dispute for a charge, if any -- backs the
        "at most one OPEN/ANALYZED dispute per charge" rule in
        `DisputeService.open_dispute`. A charge can have more than one
        dispute over time -- once a prior one is terminal (RESOLVED/
        OVERRIDDEN), a new one may be opened (e.g. the retailer amends the
        charge)."""
        row = self._session.scalars(
            select(PenaltyDispute)
            .where(
                PenaltyDispute.actual_penalty_id == actual_penalty_id,
                PenaltyDispute.dispute_status.in_(_ACTIVE_STATUSES),
            )
            .order_by(PenaltyDispute.created_at.desc())
            .limit(1)
        ).first()
        return _to_dict(row) if row is not None else None

    def list_for_purchase_order(self, purchase_order_id: UUID | None = None) -> list[dict]:
        """`purchase_order_id` given: every dispute for that PO. Omitted:
        every dispute across every PO -- mirrors
        `PenaltyProjectionRepository.list_projections`'s own optional
        `purchase_order_id` filter."""
        query = select(PenaltyDispute)
        if purchase_order_id is not None:
            query = query.where(PenaltyDispute.purchase_order_id == purchase_order_id)
        query = query.order_by(PenaltyDispute.created_at.asc())
        rows = self._session.scalars(query).all()
        return [_to_dict(r) for r in rows]

    def save_verdict(
        self,
        dispute_id: UUID,
        rule_id: UUID,
        computed_amount: float,
        delta_amount: float,
        verdict: str,
        analysis_breakdown: dict,
        analyzed_at: datetime,
    ) -> dict:
        """Persist the deterministic engine's verdict, moving the dispute to
        ANALYZED. Idempotent on re-analyze: calling this again for the same
        `dispute_id` (e.g. a rule was corrected and the dispute
        re-adjudicated) overwrites the prior verdict/breakdown rather than
        erroring -- `DisputeService.analyze` is the one place that decides
        whether re-analysis of a non-OPEN dispute is allowed."""
        row = self._get_row(dispute_id)
        if row is None:
            raise ValueError(f"No penalty dispute found with id={dispute_id!r}")

        row.rule_id = rule_id
        row.computed_amount = computed_amount
        row.delta_amount = delta_amount
        row.verdict = verdict
        row.analysis_breakdown = analysis_breakdown
        row.analyzed_at = analyzed_at
        row.dispute_status = DisputeStatus.ANALYZED
        self._session.flush()
        return _to_dict(row)

    def resolve(
        self,
        dispute_id: UUID,
        dispute_status: str,
        resolved_by: str,
        resolved_at: datetime,
        override_verdict: str | None = None,
        override_reason: str | None = None,
    ) -> dict:
        row = self._get_row(dispute_id)
        if row is None:
            raise ValueError(f"No penalty dispute found with id={dispute_id!r}")

        row.dispute_status = dispute_status
        row.resolved_by = resolved_by
        row.resolved_at = resolved_at
        row.override_verdict = override_verdict
        row.override_reason = override_reason
        self._session.flush()
        return _to_dict(row)

    def truncate_all(self) -> None:
        """Deletes every penalty_dispute row, for a force-reseed. FKs to
        actual_penalty/penalty_rule/purchase_order, so must run before those
        repositories' `truncate_all()` clear them. `penalty_summary` no
        longer FKs to this table (see its module docstring) -- ordering
        against `PenaltySummaryRepository.truncate_all()` no longer
        matters, though `PenaltySeedingService._truncate_seeded_tables`
        still runs it first for tidiness."""
        self._session.execute(delete(PenaltyDispute))
        self._session.flush()
