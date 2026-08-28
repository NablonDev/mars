"""FastAPI dependency factories for database, repository, service, and queue components."""

import logging
import os
import secrets
import socket
import threading
from collections.abc import Callable, Iterator
from datetime import date
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import LLMConfig, Settings, get_settings
from app.core.container import Container
from app.core.rate_limit import RateLimitGate, looks_like_rate_limit
from app.db.session import Database
from app.models.enums import JobRunType, JobTaskType
from app.queue.interfaces import JobDispatcher, JobSource
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_mitigation.mitigation import MitigationRepository, MitigationResultRepository
from app.repositories.fine_mitigation.summary import FineMitigationSummaryRepository
from app.repositories.fine_projection.po_delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.fine_projection.projection import ProjectionRepository
from app.repositories.fine_projection.summary import FineProjectionSummaryRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.job_queue import JobQueueRepository
from app.repositories.order import OrderRepository
from app.services.cmir_run_service import CMIRRunService
from app.services.fine_mitigation.service import FineMitigationService
from app.services.fine_mitigation.summary import FineMitigationSummaryService
from app.services.fine_projection.po_delivery_change import PoDeliveryChangeRequestService
from app.services.fine_projection.service import FineProjectionService
from app.services.fine_projection.summary import FineProjectionSummaryService
from app.services.po_validation_service import PoValidationService
from app.services.seeding.service import FineSeedingService

logger = logging.getLogger(__name__)

_JOB_ITEM_FAILURE_ERROR_CODE = "SUMMARY_GENERATION_FAILED"

# Process-local concurrency control for on-demand summary generation.
# This semaphore is shared by requests in this API process; worker processes
# maintain their own concurrency limits. It must be created once at import
# time so the limit is shared across requests.
_ON_DEMAND_SUMMARY_SEMAPHORE = threading.BoundedSemaphore(get_settings().on_demand_max_concurrent_summaries)
_ON_DEMAND_RATE_LIMIT_GATE = RateLimitGate()


def require_internal_api_key(
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Gate every non-health route behind a shared-secret header."""
    valid = False
    if x_internal_api_key is not None:
        try:
            valid = secrets.compare_digest(
                x_internal_api_key.encode("latin-1"),
                settings.internal_api_key.encode("latin-1"),
            )
        except UnicodeEncodeError:
            valid = False
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


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


def get_mitigation_repository(
    session: Session = Depends(get_session),
) -> MitigationRepository:
    return MitigationRepository(session)


def get_projection_repository(
    session: Session = Depends(get_session),
) -> ProjectionRepository:
    return ProjectionRepository(session)


def get_po_delivery_change_request_repository(
    session: Session = Depends(get_session),
) -> PoDeliveryChangeRequestRepository:
    return PoDeliveryChangeRequestRepository(session)


def get_fine_projection_summary_repository(
    session: Session = Depends(get_session),
) -> FineProjectionSummaryRepository:
    return FineProjectionSummaryRepository(session)


def get_mitigation_result_repository(
    session: Session = Depends(get_session),
) -> MitigationResultRepository:
    return MitigationResultRepository(session)


def get_fine_mitigation_summary_repository(
    session: Session = Depends(get_session),
) -> FineMitigationSummaryRepository:
    return FineMitigationSummaryRepository(session)


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
        task_type=JobTaskType.PROJECTION_SUMMARY_REGEN,
        max_attempts=settings.job_queue_max_attempts,
    )
    session.commit()

    if item is None:
        return None

    job_dispatcher.dispatch(item["id"])
    return item["id"]


def enqueue_and_dispatch_mitigation_summary_job(
    session: Session,
    job_queue_repository: JobQueueRepository,
    job_dispatcher: JobDispatcher,
    order_id: str,
    as_of_date: date,
    settings: Settings,
) -> UUID | None:
    """Persist and dispatch an on-demand mitigation-summary-generation job.

    Mirrors enqueue_and_dispatch_summary_job exactly, using
    JobTaskType.MITIGATION_SUMMARY_REGEN instead of PROJECTION_SUMMARY_REGEN.
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
        task_type=JobTaskType.MITIGATION_SUMMARY_REGEN,
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
        config = LLMConfig.from_settings(settings)
        client = AzureOpenAIChatClient(
            config,
            max_retries=config.max_retries,
            timeout_seconds=config.timeout_seconds,
        )
        app.state.llm_client = client
    return client


def get_llm_client(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AzureOpenAIChatClient:
    return get_llm_client_for_app(request.app, settings)


def get_fine_projection_service(
    orders: OrderRepository = Depends(get_order_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
) -> FineProjectionService:
    return FineProjectionService(
        orders=orders,
        rules=rules,
        master_data=master_data,
        projections=projections,
    )


def get_po_delivery_change_request_service(
    orders: OrderRepository = Depends(get_order_repository),
    po_delivery_change_requests: PoDeliveryChangeRequestRepository = Depends(
        get_po_delivery_change_request_repository
    ),
    projection_service: FineProjectionService = Depends(get_fine_projection_service),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> PoDeliveryChangeRequestService:
    return PoDeliveryChangeRequestService(
        orders=orders,
        po_delivery_change_requests=po_delivery_change_requests,
        projection_service=projection_service,
        master_data=master_data,
    )


def get_fine_mitigation_service(
    orders: OrderRepository = Depends(get_order_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
    mitigation_inputs: MitigationRepository = Depends(get_mitigation_repository),
    mitigation_results: MitigationResultRepository = Depends(get_mitigation_result_repository),
) -> FineMitigationService:
    return FineMitigationService(
        orders=orders,
        rules=rules,
        master_data=master_data,
        projections=projections,
        mitigation_inputs=mitigation_inputs,
        mitigation_results=mitigation_results,
    )


def get_fine_seeding_service(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    orders: OrderRepository = Depends(get_order_repository),
    projection_service: FineProjectionService = Depends(get_fine_projection_service),
    mitigation: MitigationRepository = Depends(get_mitigation_repository),
    po_delivery_change_service: PoDeliveryChangeRequestService = Depends(
        get_po_delivery_change_request_service
    ),
    po_delivery_change_requests: PoDeliveryChangeRequestRepository = Depends(
        get_po_delivery_change_request_repository
    ),
    job_queue_repository: JobQueueRepository = Depends(get_job_queue_repository),
) -> FineSeedingService:
    return FineSeedingService(
        master_data=master_data,
        rules=rules,
        orders=orders,
        projection_service=projection_service,
        mitigation=mitigation,
        po_delivery_change_service=po_delivery_change_service,
        po_delivery_change_requests=po_delivery_change_requests,
        job_queue=job_queue_repository,
    )


def get_fine_projection_summary_service(
    orders: OrderRepository = Depends(get_order_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
    summaries: FineProjectionSummaryRepository = Depends(get_fine_projection_summary_repository),
    prompt_registry: PromptRegistryRepository = Depends(get_prompt_registry_repository),
    llm: AzureOpenAIChatClient = Depends(get_llm_client),
) -> FineProjectionSummaryService:
    return FineProjectionSummaryService(
        orders=orders,
        rules=rules,
        master_data=master_data,
        projections=projections,
        summaries=summaries,
        prompt_registry=prompt_registry,
        llm=llm,
    )


def get_fine_mitigation_summary_service(
    orders: OrderRepository = Depends(get_order_repository),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    mitigation_results: MitigationResultRepository = Depends(get_mitigation_result_repository),
    summaries: FineMitigationSummaryRepository = Depends(get_fine_mitigation_summary_repository),
    prompt_registry: PromptRegistryRepository = Depends(get_prompt_registry_repository),
    llm: AzureOpenAIChatClient = Depends(get_llm_client),
) -> FineMitigationSummaryService:
    return FineMitigationSummaryService(
        orders=orders,
        master_data=master_data,
        mitigation_results=mitigation_results,
        summaries=summaries,
        prompt_registry=prompt_registry,
        llm=llm,
    )


def get_fine_projection_summary_job_runner(
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
                "Fine projection summary background job: on-demand concurrency cap reached "
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
                    claimed = job_queue_repository.claim_batch(worker_id, limit=1, job_item_ids=[job_item_id])
                    if not claimed:
                        logger.info(
                            "Fine projection summary background job: job_item_id=%s already claimed by "
                            "another worker; skipping",
                            job_item_id,
                        )
                        return

                _ON_DEMAND_RATE_LIMIT_GATE.wait_if_paused(threading.Event())

                service = FineProjectionSummaryService(
                    orders=OrderRepository(session),
                    rules=FineRuleRepository(session),
                    master_data=MasterDataRepository(session),
                    projections=ProjectionRepository(session),
                    summaries=FineProjectionSummaryRepository(session),
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
                            "Fine projection summary background job: rate-limit signal detected for "
                            "order_id=%s as_of_date=%s (best-effort text match -- see "
                            "looks_like_rate_limit); pausing the on-demand LLM gate for %ss",
                            order_id,
                            as_of_date,
                            settings.llm_rate_limit_backoff_seconds,
                        )
                    logger.exception(
                        "Fine projection summary background job failed: order_id=%s as_of_date=%s",
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


def get_fine_mitigation_summary_job_runner(
    database: Database = Depends(get_database),
    llm: AzureOpenAIChatClient = Depends(get_llm_client),
    settings: Settings = Depends(get_settings),
) -> Callable[[str, date, str, UUID | None], None]:
    """Return a background-safe mitigation-summary-generation runner.

    Full mirror of get_fine_projection_summary_job_runner, deliberately
    sharing the same process-local on-demand concurrency semaphore/rate-
    limit gate: both features compete for the same Azure OpenAI on-demand
    capacity budget, so a single shared cap is the correct behavior, not
    two independent ones that could double the effective on-demand
    concurrency against the same deployment.
    """
    worker_id = f"api:{socket.gethostname()}:{os.getpid()}"

    def _run(
        order_id: str,
        as_of_date: date,
        prompt_version: str,
        job_item_id: UUID | None = None,
    ) -> None:
        acquired = _ON_DEMAND_SUMMARY_SEMAPHORE.acquire(
            timeout=settings.on_demand_summary_acquire_timeout_seconds
        )
        if not acquired:
            logger.warning(
                "Fine mitigation summary background job: on-demand concurrency cap reached "
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
                    claimed = job_queue_repository.claim_batch(worker_id, limit=1, job_item_ids=[job_item_id])
                    if not claimed:
                        logger.info(
                            "Fine mitigation summary background job: job_item_id=%s already claimed by "
                            "another worker; skipping",
                            job_item_id,
                        )
                        return

                _ON_DEMAND_RATE_LIMIT_GATE.wait_if_paused(threading.Event())

                service = FineMitigationSummaryService(
                    orders=OrderRepository(session),
                    master_data=MasterDataRepository(session),
                    mitigation_results=MitigationResultRepository(session),
                    summaries=FineMitigationSummaryRepository(session),
                    prompt_registry=PromptRegistryRepository(session),
                    llm=llm,
                )
                try:
                    service.run_generation(order_id, as_of_date, prompt_version)
                except Exception as exc:
                    if looks_like_rate_limit(exc):
                        _ON_DEMAND_RATE_LIMIT_GATE.note_rate_limit_hit(
                            settings.llm_rate_limit_backoff_seconds
                        )
                        logger.warning(
                            "Fine mitigation summary background job: rate-limit signal detected for "
                            "order_id=%s as_of_date=%s (best-effort text match -- see "
                            "looks_like_rate_limit); pausing the on-demand LLM gate for %ss",
                            order_id,
                            as_of_date,
                            settings.llm_rate_limit_backoff_seconds,
                        )
                    logger.exception(
                        "Fine mitigation summary background job failed: order_id=%s as_of_date=%s",
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


# --- CMIR / PO Validation --------------------------------------------------
# Built from app.core.container.Container (a separate composition root, not
# the Depends() chain above) since the CMIR/PO-validation graphs wire up a
# shared LangGraph PostgresSaver checkpointer and long-lived resources that
# don't fit a per-request Session lifecycle. See app/core/container.py.


def build_service() -> CMIRRunService:
    """Build the production CMIR service from the project composition root."""
    container = Container.build()
    return CMIRRunService(
        email_reader=container.email_reader,
        graph=container.graph,
        agent_runs=container.agent_runs,
        workflow_threads=container.workflow_threads,
        pending_human_actions=container.pending_human_actions,
        hitl_actions=container.hitl_actions,
        hitl_state=container.hitl_state,
        email_repository=container.email_repository,
        cmir_repository=container.cmir_repository,
    )


def build_po_validation_service() -> PoValidationService:
    """Build the production PO Validation service from the project composition root."""
    container = Container.build()
    return PoValidationService(
        graph=container.po_validation_graph,
        po_lines=container.po_lines,
        material_master=container.material_master,
        po_line_errors=container.po_line_errors,
        agent_runs=container.agent_runs,
        workflow_threads=container.workflow_threads,
        pending_human_actions=container.pending_human_actions,
        hitl_actions=container.hitl_actions,
        hitl_state=container.hitl_state,
    )


def get_service(request: Request) -> CMIRRunService:
    """FastAPI dependency returning the app-instance-lifetime CMIR service.

    Memoized on `request.app.state` (not a plain lru_cache) so each FastAPI
    app instance -- including a test-created one that already carries a fake
    via create_app(service=...) -- gets its own singleton instead of sharing
    one across the process.
    """
    if request.app.state.service is None:
        request.app.state.service = build_service()
    return request.app.state.service


def get_po_service(request: Request) -> PoValidationService:
    """FastAPI dependency returning the app-instance-lifetime PO Validation service."""
    if request.app.state.po_service is None:
        request.app.state.po_service = build_po_validation_service()
    return request.app.state.po_service
