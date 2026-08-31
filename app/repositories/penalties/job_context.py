"""Repository for `penalties.penalty_job_run_context`/`penalty_job_item_context`
-- the penalties-specific extension tables over the shared
`process.job_run`/`process.job_item` queue (see
`app.models.penalties.job_context` for why these stay domain-owned and why
the table names are prefixed).

Deferred by Phase 2 to Phase 3 (this phase): these models existed from
Phase 1 with no repository. Wired in here so `ProjectionService`/
`MitigationService`/the two summary services can attach the matching
domain context row in the same transaction as the `process.job_item` row
`JobQueueRepository.enqueue()` creates.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.penalties.job_context import PenaltyJobItemContext, PenaltyJobRunContext


def _run_context_to_dict(row: PenaltyJobRunContext) -> dict:
    return {
        "job_run_id": row.job_run_id,
        "projection_date": row.projection_date,
        "stacking_mode_override": row.stacking_mode_override,
        "metadata_json": row.metadata_json,
    }


def _item_context_to_dict(row: PenaltyJobItemContext) -> dict:
    return {
        "job_item_id": row.job_item_id,
        "purchase_order_id": row.purchase_order_id,
        "projection_date": row.projection_date,
        "task_type": row.task_type,
        "stacking_mode_override": row.stacking_mode_override,
        "force_regenerate_summary": row.force_regenerate_summary,
    }


class PenaltyJobRunContextRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        job_run_id: UUID,
        projection_date: date,
        stacking_mode_override: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        row = PenaltyJobRunContext(
            job_run_id=job_run_id,
            projection_date=projection_date,
            stacking_mode_override=stacking_mode_override,
            metadata_json=metadata or {},
        )
        self._session.add(row)
        self._session.flush()
        return _run_context_to_dict(row)

    def get(self, job_run_id: UUID) -> dict | None:
        row = self._session.get(PenaltyJobRunContext, job_run_id)
        return _run_context_to_dict(row) if row is not None else None

    def truncate_all(self) -> None:
        """Deletes every penalty_job_run_context row, for a force-reseed.
        FKs to process.job_run, so must run before
        `JobQueueRepository.truncate_all()` clears it."""
        self._session.execute(delete(PenaltyJobRunContext))
        self._session.flush()


class PenaltyJobItemContextRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        job_item_id: UUID,
        purchase_order_id: UUID,
        projection_date: date,
        task_type: str,
        stacking_mode_override: str | None = None,
        force_regenerate_summary: bool = False,
    ) -> dict:
        row = PenaltyJobItemContext(
            job_item_id=job_item_id,
            purchase_order_id=purchase_order_id,
            projection_date=projection_date,
            task_type=task_type,
            stacking_mode_override=stacking_mode_override,
            force_regenerate_summary=force_regenerate_summary,
        )
        self._session.add(row)
        self._session.flush()
        return _item_context_to_dict(row)

    def get(self, job_item_id: UUID) -> dict | None:
        row = self._session.get(PenaltyJobItemContext, job_item_id)
        return _item_context_to_dict(row) if row is not None else None

    def truncate_all(self) -> None:
        """Deletes every penalty_job_item_context row, for a force-reseed.
        FKs to both process.job_item and common.purchase_order, so must run
        before `JobQueueRepository.truncate_all()`/
        `PurchaseOrderRepository.truncate_all()` clear either -- this is
        exactly the class of FK-ordering bug documented in
        `force-seeding-error.txt` (a pre-restructure job_item->order FK
        violation on force-reseed), now against the new schema's tables."""
        self._session.execute(delete(PenaltyJobItemContext))
        self._session.flush()
