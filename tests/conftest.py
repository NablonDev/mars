"""
Shared fixtures. All API/repository tests run against an in-memory
SQLite database (StaticPool keeps the single in-memory connection alive
across the whole test), never a real Postgres -- fast, no external
service required.

No composition-root class to stub here: the FastAPI app builds its own
`Database` in a `lifespan` handler, and `TestClient(app)` used outside a
`with` block never runs lifespan -- so `app.state.database` is never
populated in tests. Both `get_session` (per-request Session) and
`get_database` (the process-wide engine, used by the health check) are
overridden to point at the SQLite fixture instead.
"""

import os

# INTERNAL_API_KEY is a required setting (see app/core/config.py) with no
# default -- Settings() raises without it. setdefault() so a real value in
# the environment/.env wins, but the suite never depends on one existing:
# this must run before anything below imports app.* (app.api.dependencies
# calls get_settings() at import time). Exposed as a constant so
# tests/unit/api/test_internal_api_key.py -- which exercises the real
# dependency instead of the override below -- can assert against it.
TEST_INTERNAL_API_KEY = "test-internal-api-key-do-not-use-in-prod"
os.environ.setdefault("INTERNAL_API_KEY", TEST_INTERNAL_API_KEY)

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_database, get_job_queue, get_session, require_internal_api_key
from app.core.config import Settings
from app.db.session import Database
from app.main import create_app
from app.queue.factory import build_job_queue
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_mitigation.mitigation import MitigationRepository
from app.repositories.fine_projection.projection import ProjectionRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.order import OrderRepository
from app.services.fine_projection.service import FineProjectionService
from app.services.seeding.service import FineSeedingService


@pytest.fixture
def database() -> Database:
    db = Database(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db.create_all_tables()
    return db


@pytest.fixture
def job_queue(database: Database):
    """The default ("postgres") backend, built against the SQLite test
    `database` fixture -- `PostgresJobQueue` only ever wraps
    `JobQueueRepository` calls, which already has a SQLite-compatible
    fallback for every method (see that module's docstring), so this
    needs no real Postgres. A plain `Settings()` (not `get_settings()`)
    is used deliberately so this is never affected by whatever
    `JOB_QUEUE_BACKEND` happens to be set in the local environment/.env.
    """
    return build_job_queue(Settings(), database)


@pytest.fixture
def app(database: Database, job_queue):
    application = create_app()

    def _get_test_db():
        session = database.new_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    application.dependency_overrides[get_session] = _get_test_db
    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_job_queue] = lambda: job_queue
    # Intentional, visible override: the rest of the suite exercises business
    # logic, not the auth gate itself -- that gets its own dedicated tests in
    # tests/unit/api/test_internal_api_key.py, which remove this override.
    application.dependency_overrides[require_internal_api_key] = lambda: None
    return application


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def db_session(database: Database):
    with database.session() as session:
        yield session


@pytest.fixture
def services(db_session):
    """A lightweight bundle of repositories/services sharing one Session,
    for tests that exercise the repository/service layer directly rather
    than through HTTP. Just test plumbing (SimpleNamespace) -- production
    code wires these per-request via app/api/dependencies.py, not through a bundle."""
    master_data = MasterDataRepository(db_session)
    rules = FineRuleRepository(db_session)
    orders = OrderRepository(db_session)
    projections = ProjectionRepository(db_session)
    prompt_registry = PromptRegistryRepository(db_session)
    mitigation = MitigationRepository(db_session)
    projection_service = FineProjectionService(
        orders=orders,
        rules=rules,
        master_data=master_data,
        projections=projections,
    )
    seeding_service = FineSeedingService(
        master_data=master_data,
        rules=rules,
        orders=orders,
        projection_service=projection_service,
        mitigation=mitigation,
    )
    return SimpleNamespace(
        master_data=master_data,
        rules=rules,
        orders=orders,
        projections=projections,
        prompt_registry=prompt_registry,
        mitigation=mitigation,
        projection_service=projection_service,
        seeding_service=seeding_service,
    )


@pytest.fixture
def seeded_client(client: TestClient) -> TestClient:
    """A client with master data + the 4 example orders already seeded,
    for tests that only care about what happens *after* seeding."""
    resp = client.post("/api/v1/admin/seed-master-data")
    assert resp.status_code == 200, resp.text
    return client
