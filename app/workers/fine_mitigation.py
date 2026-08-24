"""Fine-mitigation job execution and recovery sweep.

Everything in this module is fine_mitigation-specific: the
MITIGATION_SUMMARY_REGEN per-item work (`run_mitigation_summary`) and the
recovery sweep for stranded PENDING `mitigation_summary` rows
(`sweep_stranded_pending_mitigation_summaries`, moved out of the now-removed
`sweep.py`). Mirrors `app/workers/fine_projection.py`'s shape for the
sibling domain -- mitigation itself has no nightly-enqueue equivalent
(it's on-demand only), so this module is shorter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import Settings
from app.db.session import Database
from app.models.enums import JobRunType, JobTaskType, SummaryStatus
from app.queue.interfaces import JobDispatcher
from app.queue.types import ClaimedJob, SweepResult
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_mitigation.mitigation import MitigationResultRepository
from app.repositories.fine_mitigation.summary import FineMitigationSummaryRepository
from app.repositories.job_queue import JobQueueRepository
from app.repositories.order import OrderRepository
from app.services.fine_mitigation.summary import FineMitigationSummaryService

logger = logging.getLogger(__name__)

_SWEEP_TRIGGERED_BY = "recovery-sweep"


def run_mitigation_summary(
    job: ClaimedJob,
    database: Database,
    llm: AzureOpenAIChatClient,
    *,
    heartbeat: Callable[[], None] | None,
) -> None:
    # Keep the LLM call outside the mitigation-results session's lifetime.
    with database.session() as session:
        service = FineMitigationSummaryService(
            orders=OrderRepository(session),
            master_data=MasterDataRepository(session),
            mitigation_results=MitigationResultRepository(session),
            summaries=FineMitigationSummaryRepository(session),
            prompt_registry=PromptRegistryRepository(session),
            llm=llm,
        )
        mitigation_job = service.get_or_schedule(
            job.order_id,
            as_of_date=job.projection_date,
            force_regenerate=job.force_regenerate_summary,
        )
        if mitigation_job.status == SummaryStatus.PENDING:
            service.run_generation(
                mitigation_job.order_id,
                mitigation_job.as_of_date,
                mitigation_job.prompt_version,
                heartbeat=heartbeat,
            )


def sweep_stranded_pending_mitigation_summaries(
    job_dispatcher: JobDispatcher,
    database: Database,
    settings: Settings,
    *,
    today: date | None = None,
) -> SweepResult:
    """Recover PENDING mitigation_summary rows with no
    corresponding job item -- full mirror of
    sweep_stranded_pending_projection_summaries for the mitigation-summary feature.
    Reuses the same summary_pending_sweep_days window/setting; this is a
    generic "PENDING LLM-summary row with no covering job_item" recovery
    concern, not specific to the projection-summary feature."""
    tz = ZoneInfo(settings.penalty_business_timezone)
    resolved_today = today or datetime.now(tz).date()
    earliest_as_of_date = resolved_today - timedelta(days=settings.summary_pending_sweep_days)

    with database.session() as session:
        repo = JobQueueRepository(session)
        stranded = repo.find_stranded_pending_mitigation_summaries(
            earliest_as_of_date,
            resolved_today,
        )

        if not stranded:
            return SweepResult(recovered_count=0)

        run = repo.create_run(
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
                    "task_type": JobTaskType.MITIGATION_SUMMARY_REGEN,
                }
                for row in stranded
            ],
            max_attempts=settings.job_queue_max_attempts,
        )

        if enqueued_count != len(stranded):
            repo.set_requested_item_count(job_run_id, enqueued_count)

        item_ids = [
            item["id"]
            for item in repo.list_run_items(
                job_run_id,
                limit=max(enqueued_count, 1),
            )
        ]

    for item_id in item_ids:
        job_dispatcher.dispatch(item_id)

    logger.info(
        "Recovery sweep: recovered %d stranded mitigation-summary row(s) under job_run_id=%s",
        enqueued_count,
        job_run_id,
    )

    return SweepResult(
        recovered_count=enqueued_count,
        job_run_id=job_run_id,
    )
