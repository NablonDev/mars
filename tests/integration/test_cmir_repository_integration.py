"""Integration tests for `CmirRecordRepository` against a real Postgres
instance -- unlike the unit suite, which stubs the Session itself (see
tests/unit/repositories/test_cmir_repositories.py).

Was against `PostgresCMIRRepository`/`CMIRRepository` (`app/repositories/cmir.py`,
a per-call `Database`-session repository keyed by `app.schemas.cmir.Cmir`).
Relocated onto `app.repositories.cmir.cmir_record.CmirRecordRepository`
(injected-`Session` pattern, `merged` passed as a plain dict -- see that
module's docstring for why it no longer depends on the `Cmir` schema).

Skips cleanly (not an error) when `DATABASE_URL` isn't pointed at a
reachable Postgres instance, same convention as
tests/integration/test_job_queue_postgres.py.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.db.session import Database
from app.repositories.cmir.cmir_record import CmirRecordRepository, CmirVersionConflict


def _connect_or_none() -> Database | None:
    settings = get_settings()
    if not settings.database.url.startswith("postgresql"):
        return None

    db = Database(settings.database.url)
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        db.dispose()
        return None
    return db


@pytest.fixture(scope="module")
def pg_database():
    db = _connect_or_none()
    if db is None:
        pytest.skip(
            "No reachable Postgres DATABASE_URL configured -- skipping CMIR-repository Postgres "
            "integration tests."
        )
    db.create_all_tables()
    yield db
    db.dispose()


@pytest.fixture
def identity():
    """Unique per test so repeated runs against a shared dev database never collide."""
    return f"Integration Test Customer {uuid4().hex[:8]}"


@pytest.fixture
def material_ref():
    return f"MAT-{uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _cleanup(pg_database: Database, identity: str):
    yield
    with pg_database.session() as session:
        session.execute(
            text("DELETE FROM cmir.cmir_record WHERE customer_identity = :customer_identity"),
            {"customer_identity": identity},
        )


def _merged(*, customer_identity: str, target_customer_material_ref: str, brand: str) -> dict:
    return {
        "sender_type": "EMAIL",
        "customer_identity": customer_identity,
        "material_identity": "MAT-IDENTITY",
        "intent_phrase": None,
        "existing_cmir_ref": "",
        "brand": brand,
        "site": "",
        "target_grd_code": "",
        "target_customer_material_ref": target_customer_material_ref,
        "effective_date": None,
        "reason": None,
    }


def test_supersede_and_insert_then_get_current_round_trips(
    pg_database: Database, identity: str, material_ref: str
):
    with pg_database.session() as session:
        repo = CmirRecordRepository(session)

        new_id = repo.supersede_and_insert(
            customer_identity=identity,
            target_customer_material_ref=material_ref,
            merged=_merged(
                customer_identity=identity,
                target_customer_material_ref=material_ref,
                brand="IntegrationBrand",
            ),
            expected_current_id=None,
        )

        current = repo.get_current(identity, material_ref)
        assert current is not None
        assert current["id"] == new_id
        assert current["brand"] == "IntegrationBrand"


def test_supersede_and_insert_retires_previous_current_row(
    pg_database: Database, identity: str, material_ref: str
):
    with pg_database.session() as session:
        repo = CmirRecordRepository(session)

        first_id = repo.supersede_and_insert(
            customer_identity=identity,
            target_customer_material_ref=material_ref,
            merged=_merged(
                customer_identity=identity, target_customer_material_ref=material_ref, brand="FirstVersion"
            ),
            expected_current_id=None,
        )

        second_id = repo.supersede_and_insert(
            customer_identity=identity,
            target_customer_material_ref=material_ref,
            merged=_merged(
                customer_identity=identity, target_customer_material_ref=material_ref, brand="SecondVersion"
            ),
            expected_current_id=first_id,
        )

        current = repo.get_current(identity, material_ref)
        assert current is not None
        assert current["id"] == second_id
        assert current["brand"] == "SecondVersion"


def test_supersede_and_insert_raises_conflict_on_stale_expected_id(
    pg_database: Database, identity: str, material_ref: str
):
    with pg_database.session() as session:
        repo = CmirRecordRepository(session)

        repo.supersede_and_insert(
            customer_identity=identity,
            target_customer_material_ref=material_ref,
            merged=_merged(
                customer_identity=identity, target_customer_material_ref=material_ref, brand="OnlyVersion"
            ),
            expected_current_id=None,
        )

        with pytest.raises(CmirVersionConflict):
            repo.supersede_and_insert(
                customer_identity=identity,
                target_customer_material_ref=material_ref,
                merged=_merged(
                    customer_identity=identity, target_customer_material_ref=material_ref, brand="OnlyVersion"
                ),
                expected_current_id=None,  # stale: a current row already exists now
            )
