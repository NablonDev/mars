"""FastAPI dependency factories for database, repository, service, and queue components."""

import logging
import os
import socket
import threading
from collections.abc import Callable, Iterator
from datetime import date
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from sqlalchemy.orm import Session

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimitGate, looks_like_rate_limit
from app.db.session import Database
from app.models.enums import JobRunType, JobTaskType
from app.queue.interfaces import JobDispatcher, JobSource
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.job_queue import JobQueueRepository
from app.repositories.master_data import MasterDataRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.services.fine_summary import FineSummaryService
from app.services.projection import ProjectionService
from app.services.seeding import SeedingService

logger = logging.getLogger(__name__)

_JOB_ITEM_FAILURE_ERROR_CODE = "SUMMARY_GENERATION_FAILED"

# Process-local concurrency control for on-demand summary generation.
# This semaphore is shared by requests in this API process; worker processes
# maintain their own concurrency limits. It must be created once at import
# time so the limit is shared across requests.
_ON_DEMAND_SUMMARY_SEMAPHORE = threading.BoundedSemaphore(
    get_settings().on_demand_max_concurrent_summaries
)
_ON_DEMAND_RATE_LIMIT_GATE = RateLimitGate()


def get_database(request: Request) -> Database:
    """Return the process-wide Database created during application startup."""
    return request.app.state.database


def get_session(database: Database = Depends(get_database)) -> Iterator[Session]:
    session = database.new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_master_data_repository(
    session: Session = Depends(get_session),
) -> MasterDataRepository:
    return MasterDataRepository(session)


def get_fine_rule_repository(
    session: Session = Depends(get_session),
) -> FineRuleRepository:
    return FineRuleRepository(session)


def get_order_repository(
    session: Session = Depends(get_session),
) -> OrderRepository:
    return OrderRepository(session)


def get_projection_repository(
    session: Session = Depends(get_session),
) -> ProjectionRepository:
    return ProjectionRepository(session)


def get_fine_summary_repository(
    session: Session = Depends(get_session),
) -> FineSummaryRepository:
    return FineSummaryRepository(session)


def get_prompt_registry_repository(
    session: Session = Depends(get_session),
) -> PromptRegistryRepository:
    return PromptRegistryRepository(session)


def get_job_queue(request: Request) -> tuple[JobDispatcher, JobSource]:
    """Return the application-scoped job dispatcher and source."""
    return request.app.state.job_queue


def get_job_dispatcher(
    job_queue: tuple[JobDispatcher, JobSource] = Depends(get_job_queue),
) -> JobDispatcher:
    return job_queue[0]


def get_job_queue_repository(
    session: Session = Depends(get_session),
) -> JobQueueRepository:
    return JobQueueRepository(session)


def enqueue_and_dispatch_summary_job(
    session: Session,
    job_queue_repository: JobQueueRepository,
    job_dispatcher: JobDispatcher,
    order_id: str,
    as_of_date: date,
    settings: Settings,
) -> UUID | None:
    """Persist and dispatch an on-demand summary-generation job.

    The job is committed using the caller's session before dispatch.
    Returns the job item ID, or None when enqueueing loses its documented
    race with an existing job.
    """
    run = job_queue_repository.create_run(
        run_type=JobRunType.ON_DEMAND,
        projection_date=as_of_date,
        requested_item_count=1,
        triggered_by="api",
    )
    item = job_queue_repository.enqueue(
        job_run_id=run["id"],
        order_id=order_id,
        projection_date=as_of_date,
        task_type=JobTaskType.SUMMARY_REGEN,
        max_attempts=settings.job_queue_max_attempts,
    )
    session.commit()

    if item is None:
        return None

    job_dispatcher.dispatch(item["id"])
    return item["id"]


def get_llm_client_for_app(
    app: FastAPI,
    settings: Settings,
) -> AzureOpenAIChatClient:
    """Return the application-scoped LLM client, creating it on first use."""
    client = getattr(app.state, "llm_client", None)
    if client is None:
        client = AzureOpenAIChatClient(
            settings,
            max_retries=settings.azure_openai_max_attempts,
            timeout_seconds=settings.azure_openai_timeout_seconds,
        )
        app.state.llm_client = client
    return client


def get_llm_client(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AzureOpenAIChatClient:
    return get_llm_client_for_app(request.app, settings)


def get_projection_service(
    orders: OrderRepository = Depends(get_order_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
) -> ProjectionService:
    return ProjectionService(
        orders=orders,
        rules=rules,
        master_data=master_data,
        projections=projections,
    )


def get_seeding_service(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    orders: OrderRepository = Depends(get_order_repository),
    projection_service: ProjectionService = Depends(get_projection_service),
) -> SeedingService:
    return SeedingService(
        master_data=master_data,
        rules=rules,
        orders=orders,
        projection_service=projection_service,
    )


def get_fine_summary_service(
    orders: OrderRepository = Depends(get_order_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
    summaries: FineSummaryRepository = Depends(get_fine_summary_repository),
    prompt_registry: PromptRegistryRepository = Depends(get_prompt_registry_repository),
    llm: AzureOpenAIChatClient = Depends(get_llm_client),
) -> FineSummaryService:
    return FineSummaryService(
        orders=orders,
        rules=rules,
        master_data=master_data,
        projections=projections,
        summaries=summaries,
        prompt_registry=prompt_registry,
        llm=llm,
    )


def get_fine_summary_job_runner(
    database: Database = Depends(get_database),
    llm: AzureOpenAIChatClient = Depends(get_llm_client),
    settings: Settings = Depends(get_settings),
) -> Callable[[str, date, str, UUID | None], None]:
    """Return a background-safe summary-generation runner.

    The runner creates its own database session and optionally claims and
    settles a queue item. Concurrency is limited before claiming so a queued
    item is not left RUNNING while waiting for an execution slot.
    """
    worker_id = f"api:{socket.gethostname()}:{os.getpid()}"

    def _run(
        order_id: str,
        as_of_date: date,
        prompt_version: str,
        job_item_id: UUID | None = None,
    ) -> None:
        # Acquire before claiming so a RUNNING item never waits for a
        # concurrency slot. On timeout, leave the item PENDING for the
        # nightly batch rather than claiming it and returning.
        acquired = _ON_DEMAND_SUMMARY_SEMAPHORE.acquire(
            timeout=settings.on_demand_summary_acquire_timeout_seconds
        )
        if not acquired:
            logger.warning(
                "Fine summary background job: on-demand concurrency cap reached "
                "(on_demand_max_concurrent_summaries=%s, acquire timeout=%ss); not "
                "claiming job_item_id=%s (order_id=%s as_of_date=%s) -- leaving it "
                "PENDING for the nightly batch",
                settings.on_demand_max_concurrent_summaries,
                settings.on_demand_summary_acquire_timeout_seconds,
                job_item_id,
                order_id,
                as_of_date,
            )
            return

        try:
            with database.session() as session:
                job_queue_repository = JobQueueRepository(session)

                if job_item_id is not None:
                    claimed = job_queue_repository.claim_batch(
                        worker_id,
                        limit=1,
                        job_item_ids=[job_item_id]
                    )
                    if not claimed:
                        logger.info(
                            "Fine summary background job: job_item_id=%s already claimed by "
                            "another worker; skipping",
                            job_item_id,
                        )
                        return

                _ON_DEMAND_RATE_LIMIT_GATE.wait_if_paused(threading.Event())

                service = FineSummaryService(
                    orders=OrderRepository(session),
                    rules=FineRuleRepository(session),
                    master_data=MasterDataRepository(session),
                    projections=ProjectionRepository(session),
                    summaries=FineSummaryRepository(session),
                    prompt_registry=PromptRegistryRepository(session),
                    llm=llm,
                )
                try:
                    service.run_generation(order_id, as_of_date, prompt_version)
                except Exception as exc:
                    # BackgroundTasks does not provide queue-worker retry
                    # semantics. Record the failure and settle the item as
                    # DEAD; the nightly batch provides the retry path.
                    if looks_like_rate_limit(exc):
                        _ON_DEMAND_RATE_LIMIT_GATE.note_rate_limit_hit(
                            settings.llm_rate_limit_backoff_seconds
                        )
                        logger.warning(
                            "Fine summary background job: rate-limit signal detected for "
                            "order_id=%s as_of_date=%s (best-effort text match -- see "
                            "looks_like_rate_limit); pausing the on-demand LLM gate for %ss",
                            order_id,
                            as_of_date,
                            settings.llm_rate_limit_backoff_seconds,
                        )
                    logger.exception(
                        "Fine summary background job failed: order_id=%s as_of_date=%s",
                        order_id,
                        as_of_date,
                    )
                    if job_item_id is not None:
                        job_queue_repository.mark_dead(
                            job_item_id,
                            worker_id,
                            error=str(exc),
                            error_code=_JOB_ITEM_FAILURE_ERROR_CODE,
                        )
                    return

                if job_item_id is not None:
                    job_queue_repository.mark_succeeded(job_item_id, worker_id)
        finally:
            _ON_DEMAND_SUMMARY_SEMAPHORE.release()

    return _run
