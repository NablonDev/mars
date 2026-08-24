"""Framework-independent types shared across queue backends.

This module must not depend on SQLAlchemy or a transport SDK. The types form
the data contract between durable job storage and backend-specific dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ClaimedJob:
    """Immutable representation of a job claimed for execution.

    `receipt` contains opaque backend-specific settlement state. Only the
    backend that created the claim may interpret it.
    """

    job_item_id: UUID
    job_run_id: UUID
    order_id: str
    projection_date: date
    task_type: str
    stacking_mode_override: str | None
    force_regenerate_summary: bool
    attempt_count: int
    max_attempts: int
    receipt: object | None = None


@dataclass
class SweepResult:
    """Result of one domain's stranded-PENDING-summary recovery sweep.

    Shared by domain workers to provide a consistent, domain-neutral result.
    Used to report recovered jobs and the associated job run, when available.
    """

    recovered_count: int
    job_run_id: UUID | None = None


def claimed_job_from_row(
    row: dict[str, Any],
    *,
    receipt: object | None = None,
) -> ClaimedJob:
    """Convert a repository row into the backend-neutral job contract."""
    return ClaimedJob(
        job_item_id=row["id"],
        job_run_id=row["job_run_id"],
        order_id=row["order_id"],
        projection_date=row["projection_date"],
        task_type=row["task_type"],
        stacking_mode_override=row["stacking_mode_override"],
        force_regenerate_summary=row["force_regenerate_summary"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        receipt=receipt,
    )
