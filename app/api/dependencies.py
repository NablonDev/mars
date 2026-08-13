"""FastAPI dependency factories for database sessions, repositories, and services."""

from collections.abc import Callable, Iterator
from datetime import date

from fastapi import Depends, FastAPI, Request
from sqlalchemy.orm import Session

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import Settings, get_settings
from app.db.session import Database
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.master_data import MasterDataRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.services.fine_summary import FineSummaryService
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
