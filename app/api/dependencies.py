"""FastAPI dependency factories for database sessions, repositories, and services."""

from collections.abc import Callable, Iterator
from datetime import date

from fastapi import Depends, FastAPI, Request
from sqlalchemy.orm import Session

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import Settings, get_settings
from app.core.container import Container
from app.db.session import Database
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.master_data import MasterDataRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.services.cmir_run_service import CMIRRunService
from app.services.fine_summary import FineSummaryService
from app.services.po_validation_service import PoValidationService
from app.services.projection import ProjectionService
from app.services.seeding import SeedingService


def get_database(request: Request) -> Database:
    """Return the process-wide Database created during application startup."""
    return request.app.state.database


def get_db(database: Database = Depends(get_database)) -> Iterator[Session]:
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
    session: Session = Depends(get_db),
) -> MasterDataRepository:
    return MasterDataRepository(session)


def get_fine_rule_repository(
    session: Session = Depends(get_db),
) -> FineRuleRepository:
    return FineRuleRepository(session)


def get_order_repository(
    session: Session = Depends(get_db),
) -> OrderRepository:
    return OrderRepository(session)


def get_projection_repository(
    session: Session = Depends(get_db),
) -> ProjectionRepository:
    return ProjectionRepository(session)


def get_fine_summary_repository(
    session: Session = Depends(get_db),
) -> FineSummaryRepository:
    return FineSummaryRepository(session)


def get_prompt_registry_repository(
    session: Session = Depends(get_db),
) -> PromptRegistryRepository:
    return PromptRegistryRepository(session)


def get_llm_client_for_app(
    app: FastAPI,
    settings: Settings,
) -> AzureOpenAIChatClient:
    """Create and cache the LLM client lazily on application state."""
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
) -> Callable[[str, date, str], None]:
    """Return a background-task-safe callable that opens its own database session."""

    def _run(order_id: str, as_of_date: date, prompt_version: str) -> None:
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
            service.run_generation(order_id, as_of_date, prompt_version)

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
