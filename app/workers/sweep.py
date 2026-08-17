"""Recover stranded PENDING fine-summary jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.db.session import Database
from app.models.enums import JobRunType, JobTaskType
from app.queue.interfaces import JobDispatcher
from app.repositories.job_queue import JobQueueRepository

logger = logging.getLogger(__name__)

_SWEEP_TRIGGERED_BY = "recovery-sweep"


@dataclass
class SweepResult:
    recovered_count: int
    job_run_id: UUID | None = None


def sweep_stranded_pending_summaries(
    job_dispatcher: JobDispatcher,
    database: Database,
    settings: Settings,
    *,
    today: date | None = None,
) -> SweepResult:
    """Recover PENDING summary rows with no corresponding job item.

    Dispatches only after the DB transaction commits.
    """
    tz = ZoneInfo(settings.penalty_business_timezone)
    resolved_today = today or datetime.now(tz).date()
    earliest_as_of_date = resolved_today - timedelta(
        days=settings.summary_pending_sweep_days
    )

    with database.session() as session:
        repo = JobQueueRepository(session)
        stranded = repo.find_stranded_pending_summaries(
            earliest_as_of_date,
            resolved_today,
        )

        if not stranded:
            return SweepResult(recovered_count=0)

        run = repo.create_run(
            # Use MANUAL_BATCH; triggered_by identifies the recovery sweep.
            run_type=JobRunType.MANUAL_BATCH,
            projection_date=resolved_today,
            triggered_by=_SWEEP_TRIGGERED_BY,
            requested_item_count=len(stranded),
        )
        job_run_id = run["id"]

        enqueued_count = repo.enqueue_many(
            job_run_id,
            [
                {
                    "order_id": row["order_id"],
                    "projection_date": row["as_of_date"],
                    "task_type": JobTaskType.SUMMARY_REGEN,
                }
                for row in stranded
            ],
            max_attempts=settings.job_queue_max_attempts,
        )

        # Keep the run count aligned with items actually created.
        if enqueued_count != len(stranded):
            repo.set_requested_item_count(job_run_id, enqueued_count)

        item_ids = [
            item["id"]
            for item in repo.list_run_items(
                job_run_id,
                limit=max(enqueued_count, 1),
            )
        ]

    # Commit before dispatch so workers can safely claim the items.
    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    logger.info(
        "Recovery sweep: recovered %d stranded summary row(s) under job_run_id=%s",
        enqueued_count,
        job_run_id,
    )

    return SweepResult(
        recovered_count=enqueued_count,
        job_run_id=job_run_id,
    )
