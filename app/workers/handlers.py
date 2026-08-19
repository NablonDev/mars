"""Execute one claimed job item."""

from __future__ import annotations

from collections.abc import Callable

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import Settings
from app.db.session import Database
from app.models.enums import JobTaskType, SummaryStatus
from app.queue.types import ClaimedJob
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.master_data import MasterDataRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.services.fine_summary import FineSummaryService
from app.services.projection import ProjectionService


def execute_job(
    job: ClaimedJob,
    database: Database,
    # Part of the fixed handler contract (see loop.py's execute_job_fn seam) --
    # unused today, kept so a future settings-driven knob needs no signature change.
    settings: Settings,
    llm: AzureOpenAIChatClient,
    heartbeat: Callable[[], None] | None = None,
) -> None:
    """Execute one job; exception classification is handled by the worker loop.

    ORDER_RUN runs projection then summary. SUMMARY_REGEN runs summary only
    and requires an existing projection.
    """
    if job.task_type == JobTaskType.ORDER_RUN:
        _run_projection(job, database)
        _run_summary(job, database, llm, heartbeat=heartbeat)
    elif job.task_type == JobTaskType.SUMMARY_REGEN:
        _run_summary(job, database, llm, heartbeat=heartbeat)
    else:
        # Unknown task types are non-retryable.
        raise ValueError(f"Unknown task_type={job.task_type!r} for job_item_id={job.job_item_id}")


def _run_projection(job: ClaimedJob, database: Database) -> None:
    with database.session() as session:
        service = ProjectionService(
            orders=OrderRepository(session),
            rules=FineRuleRepository(session),
            master_data=MasterDataRepository(session),
            projections=ProjectionRepository(session),
        )
        service.run_for_order(job.order_id, job.projection_date, job.stacking_mode_override)


def _run_summary(
    job: ClaimedJob,
    database: Database,
    llm: AzureOpenAIChatClient,
    *,
    heartbeat: Callable[[], None] | None,
) -> None:
    # Keep the LLM call outside the projection session's lifetime.
    with database.session() as session:
        service = FineSummaryService(
            orders=OrderRepository(session),
            rules=FineRuleRepository(session),
            master_data=MasterDataRepository(session),
            projections=ProjectionRepository(session),
            summaries=FineSummaryRepository(session),
            prompt_registry=PromptRegistryRepository(session),
            llm=llm,
        )
        fine_job = service.get_or_schedule(
            job.order_id,
            as_of_date=job.projection_date,
            force_regenerate=job.force_regenerate_summary,
        )
        if fine_job.status == SummaryStatus.PENDING:
            # run_generation persists failure and re-raises for worker classification.
            service.run_generation(
                fine_job.order_id,
                fine_job.as_of_date,
                fine_job.prompt_version,
                heartbeat=heartbeat,
            )
