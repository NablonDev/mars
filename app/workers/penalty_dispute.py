"""Dispute-summary job execution -- mirrors
`app.workers.penalty_projection.run_summary`'s shape. The persisted
`penalty_summary` row itself is keyed exactly like PROJECTION/MITIGATION,
`(purchase_order_id, summary_type, as_of_date)` -- see `app.services.
penalties.dispute.summary_service`'s module docstring. The one thing that
is still `dispute_id`-keyed is how this job item itself is addressed: the
dispute id is read from `process.job_item.metadata_json`
(`{"dispute_id": "..."}`, set at enqueue time by `DisputeSummaryService.
_enqueue_regeneration_job`) rather than from `penalty_job_item_context` --
that table gets no dispute-specific column (see `app.models.penalties.
job_context.PenaltyJobItemContext`'s docstring). `PenaltyJobItemContextRepository`
is still consulted for `force_regenerate_summary`, populated for every
task type including this one.

No batch job type / recovery sweep for the deterministic `analyze()` step
itself -- it stays synchronous/on-demand via the API (locked design
decision); only the LLM narrative is ever queued.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.db.session import Database
from app.models.enums import SummaryStatus
from app.queue.types import ClaimedJob
from app.repositories.common.master_data import MasterDataRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.dispute import PenaltyDisputeRepository
from app.repositories.penalties.job_context import PenaltyJobItemContextRepository
from app.repositories.penalties.rule import PenaltyRuleRepository
from app.repositories.penalties.summary import PenaltySummaryRepository
from app.repositories.process.agent_registry import AgentRegistryRepository
from app.repositories.process.job_queue import JobQueueRepository
from app.services.penalties.dispute.summary_service import DisputeSummaryService


def _missing_metadata_error(job_item_id) -> ValueError:
    """Non-retryable -- see `app.workers.penalty_projection`'s sibling of
    the same name."""
    return ValueError(
        f"No dispute_id found on process.job_item.metadata for job_item_id={job_item_id!r} -- "
        "cannot execute this DISPUTE_SUMMARY_REGEN job."
    )


def run_dispute_summary(
    job: ClaimedJob,
    database: Database,
    llm: AzureOpenAIChatClient,
    *,
    heartbeat: Callable[[], None] | None,
) -> None:
    with database.session() as session:
        job_item = JobQueueRepository(session).get_item(job.job_item_id)
        raw_dispute_id = job_item["metadata_json"].get("dispute_id") if job_item is not None else None
        if raw_dispute_id is None:
            raise _missing_metadata_error(job.job_item_id)
        dispute_id = UUID(str(raw_dispute_id))

        context = PenaltyJobItemContextRepository(session).get(job.job_item_id)
        force_regenerate = bool(context["force_regenerate_summary"]) if context is not None else False

        service = DisputeSummaryService(
            purchase_orders=PurchaseOrderRepository(session),
            summaries=PenaltySummaryRepository(session),
            agent_registry=AgentRegistryRepository(session),
            job_queue=JobQueueRepository(session),
            job_context=PenaltyJobItemContextRepository(session),
            llm=llm,
            disputes=PenaltyDisputeRepository(session),
            rules=PenaltyRuleRepository(session),
            master_data=MasterDataRepository(session),
        )
        summary_job = service.get_or_schedule_for_dispute(dispute_id, force_regenerate=force_regenerate)
        if summary_job.status == SummaryStatus.PENDING:
            # Same explicit-commit-before-reraise reasoning as
            # `app.workers.penalty_projection.run_summary` -- see that
            # function's inline comment.
            try:
                service.run_generation(
                    summary_job.purchase_order_id, summary_job.as_of_date, heartbeat=heartbeat
                )
            except Exception:
                session.commit()
                raise
