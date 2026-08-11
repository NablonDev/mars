"""
Shared fixtures. All API/repository tests run against an in-memory
SQLite database (StaticPool keeps the single in-memory connection alive
across the whole test), never a real Postgres -- fast, no external
service required.

No composition-root class to stub here: the FastAPI app builds its own
`Database` in a `lifespan` handler, and `TestClient(app)` used outside a
`with` block never runs lifespan -- so `app.state.database` is never
populated in tests. Both `get_db` (per-request Session) and
`get_database` (the process-wide engine, used by the health check) are
overridden to point at the SQLite fixture instead.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_database, get_db
from app.db.session import Database
from app.main import create_app
from app.repositories.fine_rule_repository import FineRuleRepository
from app.repositories.master_data_repository import MasterDataRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.projection_repository import ProjectionRepository
from app.services.projection_service import ProjectionService
from app.services.seeding_service import SeedingService


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
def app(database: Database):
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

    application.dependency_overrides[get_db] = _get_test_db
    application.dependency_overrides[get_database] = lambda: database
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
    projection_service = ProjectionService(
        orders=orders,
        rules=rules,
        master_data=master_data,
        projections=projections,
    )
    seeding_service = SeedingService(
        master_data=master_data,
        rules=rules,
        orders=orders,
        projection_service=projection_service,
    )
    return SimpleNamespace(
        master_data=master_data,
        rules=rules,
        orders=orders,
        projections=projections,
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
