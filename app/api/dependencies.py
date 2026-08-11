"""
FastAPI dependencies. `get_db` opens one Session per request (stashed on
`request.app.state.database`, built once at startup in main.py) and
closes it when the request ends, committing on success. Every other
dependency here is a small factory chained off `get_db` -- FastAPI caches
a dependency's result per request, so five endpoints each depending on
`get_order_repository` still share the one Session `get_db` opened.

No composition-root class here on purpose: FastAPI's own dependency
injection does the wiring, per docs/ARCHITECTURE.md's reference layout.
"""

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.agents.base import StructuredChatClient
from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import Settings, get_settings
from app.db.session import Database
from app.repositories.explanation_repository import ExplanationRepository
from app.repositories.fine_rule_repository import FineRuleRepository
from app.repositories.master_data_repository import MasterDataRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.projection_repository import ProjectionRepository
from app.services.explanation_service import ExplanationService
from app.services.projection_service import ProjectionService
from app.services.seeding_service import SeedingService


def get_database(request: Request) -> Database:
    """The one process-wide `Database` (engine + connection pool), built in
    app/main.py's `lifespan`. The single place that reaches into
    `app.state` for it -- `get_db` and the health check both go through
    here."""
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


def get_master_data_repository(session: Session = Depends(get_db)) -> MasterDataRepository:
    return MasterDataRepository(session)


def get_fine_rule_repository(session: Session = Depends(get_db)) -> FineRuleRepository:
    return FineRuleRepository(session)


def get_order_repository(session: Session = Depends(get_db)) -> OrderRepository:
    return OrderRepository(session)


def get_projection_repository(session: Session = Depends(get_db)) -> ProjectionRepository:
    return ProjectionRepository(session)


def get_explanation_repository(session: Session = Depends(get_db)) -> ExplanationRepository:
    return ExplanationRepository(session)


def get_llm_client(request: Request, settings: Settings = Depends(get_settings)) -> StructuredChatClient:
    """Built once per process and cached on `app.state.llm_client` --
    matching how `app/main.py::create_app` builds the one `Database`
    instance -- since `AzureChatOpenAI`'s constructor opens its own
    underlying HTTP connection pool, and there's no reason to open a new
    one per request. Still built lazily (first request that needs it, not
    app startup) rather than eagerly in `create_app`, so
    `AzureOpenAIConfigError` continues to only surface once a caller
    actually needs the LLM, not at app-startup/health-check time in
    environments without Azure credentials configured -- see
    app/agents/providers/azure_openai.py."""
    client = getattr(request.app.state, "llm_client", None)
    if client is None:
        client = AzureOpenAIChatClient(
            settings,
            max_attempts=settings.azure_openai_max_attempts,
            timeout_seconds=settings.azure_openai_timeout_seconds,
        )
        request.app.state.llm_client = client
    return client


def get_projection_service(
    orders: OrderRepository = Depends(get_order_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
) -> ProjectionService:
    return ProjectionService(orders=orders, rules=rules, master_data=master_data, projections=projections)


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


def get_explanation_service(
    orders: OrderRepository = Depends(get_order_repository),
    rules: FineRuleRepository = Depends(get_fine_rule_repository),
    master_data: MasterDataRepository = Depends(get_master_data_repository),
    projections: ProjectionRepository = Depends(get_projection_repository),
    explanations: ExplanationRepository = Depends(get_explanation_repository),
    llm: StructuredChatClient = Depends(get_llm_client),
) -> ExplanationService:
    return ExplanationService(
        orders=orders,
        rules=rules,
        master_data=master_data,
        projections=projections,
        explanations=explanations,
        llm=llm,
    )
