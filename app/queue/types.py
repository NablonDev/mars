"""Framework-independent types shared across queue backends.

This module must not depend on SQLAlchemy or a transport SDK. The types form
the data contract between durable job storage and backend-specific dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ClaimedJob:
    """Immutable representation of a job claimed for execution.

    `receipt` contains opaque backend-specific settlement state. Only the
    backend that created the claim may interpret it.

    The domain-shaped business key this dataclass used to carry directly
    (`order_id`/`projection_date`/`task_type`/`stacking_mode_override`/
    `force_regenerate_summary`) is gone -- it moved off `process.job_item`
    entirely, onto each domain's own `job_item_context` extension table
    (see `app.repositories.process.job_queue`'s module docstring). A
    caller that needs those fields (e.g. `app.workers.penalty_projection`/
    `penalty_mitigation`) looks up the matching context row itself, keyed
    on `job_item_id`, rather than reading it off this dataclass.
    `item_type` (was `task_type`) is `process.job_item.item_type`, the real
    discriminator column, and is still carried here since every backend's
    dispatch table needs it to route the claimed job at all.
    """

    job_item_id: UUID
    job_run_id: UUID
    item_type: str
    dedupe_key: str | None
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
        item_type=row["item_type"],
        dedupe_key=row["dedupe_key"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        receipt=receipt,
    )
