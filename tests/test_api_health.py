"""
GET /api/v1/health, both branches.

The 503 branch is exercised with a `Database` double whose
`engine.connect()` raises -- no Postgres is stopped, started, or otherwise
touched to produce it.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.dependencies import get_database

# Shaped like a real psycopg failure, DSN and all -- the point of the test
# is that none of this reaches the response body.
DEAD_DSN_ERROR = OperationalError(
    "SELECT 1",
    {},
    Exception(
        'connection to server at "db.internal" (10.0.0.9), port 5432 failed: '
        "postgresql://svc_user:sup3rs3cret@db.internal:5432/mars"
    ),
)


class _UnreachableEngine:
    def connect(self):
        raise DEAD_DSN_ERROR


class _UnreachableDatabase:
    """Only `engine` is needed -- the health check deliberately doesn't open
    an ORM Session, just a raw pooled connection."""

    engine = _UnreachableEngine()


@pytest.fixture
def unhealthy_client(app) -> TestClient:
    app.dependency_overrides[get_database] = _UnreachableDatabase
    return TestClient(app)


def test_health_returns_200_when_the_database_answers(client):
    resp = client.get("/api/v1/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_the_database_is_unreachable(unhealthy_client):
    resp = unhealthy_client.get("/api/v1/health")

    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "database": "unreachable"}


def test_health_does_not_leak_connection_detail_on_failure(unhealthy_client):
    resp = unhealthy_client.get("/api/v1/health")

    assert "sup3rs3cret" not in resp.text
    assert "db.internal" not in resp.text
    assert "OperationalError" not in resp.text


def test_health_returns_its_connection_to_the_pool(client):
    """The SQLite test fixture uses a `StaticPool` holding exactly one
    connection, so a health check that failed to release it would starve
    every later request. Repeated calls followed by a real query prove the
    `with` block does its job."""
    for _ in range(5):
        assert client.get("/api/v1/health").status_code == 200

    assert client.get("/api/v1/orders").status_code == 200
