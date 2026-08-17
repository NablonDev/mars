from __future__ import annotations

import os
import unittest
from uuid import uuid4

from sqlalchemy import make_url, text
from sqlalchemy.exc import OperationalError

import app.models  # noqa: F401 -- import every ORM model so Base.metadata is fully populated
from app.db.base import Base
from app.db.session import Database
from app.repositories.cmir import CMIRVersionConflict, PostgresCMIRRepository
from app.schemas.cmir import CMIR

_DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://cmir_user:cmir_pass@localhost:5432/cmir_db"


def _test_database_url() -> str:
    return os.getenv("DATABASE_URL", _DEFAULT_TEST_DATABASE_URL)


class PostgresCMIRRepositoryIntegrationTests(unittest.TestCase):
    """Exercises PostgresCMIRRepository against a real Postgres connection and a
    real SQLAlchemy session -- unlike the unit suite, which stubs the Session
    itself (see tests/unit/repositories/test_cmir_repositories.py).

    Requires a reachable Postgres at DATABASE_URL (the docker-compose `db`
    service works out of the box: `docker compose up -d db`). Skips rather
    than failing when no database is reachable, so `python -m unittest
    discover -s tests` still runs without a live Postgres connection, per
    this repo's existing testing convention (see CLAUDE.local.md).
    """

    @classmethod
    def setUpClass(cls) -> None:
        database_url = _test_database_url()
        cls.database = Database(database_url)
        try:
            with cls.database.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except OperationalError as exc:
            host = make_url(database_url).render_as_string(hide_password=True)
            raise unittest.SkipTest(
                f"Postgres not reachable at {host} -- "
                f"run `docker compose up -d db` to enable this test ({exc})"
            )
        Base.metadata.create_all(cls.database.engine)

    def setUp(self) -> None:
        self.repository = PostgresCMIRRepository(self.database)
        # Unique per test run so repeated runs against a shared dev database never collide.
        self.customer_identity = f"Integration Test Customer {uuid4().hex[:8]}"
        self.material_ref = f"MAT-{uuid4().hex[:8]}"

    def tearDown(self) -> None:
        with self.database.session() as session:
            session.execute(
                text("DELETE FROM cmir_records WHERE customer_identity = :customer_identity"),
                {"customer_identity": self.customer_identity},
            )

    def test_supersede_and_insert_then_get_current_round_trip(self) -> None:
        cmir = CMIR(
            customer_identity=self.customer_identity,
            target_customer_material_ref=self.material_ref,
            brand="IntegrationBrand",
        )

        new_id = self.repository.supersede_and_insert(
            customer_identity=self.customer_identity,
            target_customer_material_ref=self.material_ref,
            merged=cmir,
            expected_current_id=None,
        )

        current = self.repository.get_current(self.customer_identity, self.material_ref)
        self.assertIsNotNone(current)
        self.assertEqual(current["id"], new_id)
        self.assertEqual(current["brand"], "IntegrationBrand")

    def test_supersede_and_insert_retires_previous_current_row(self) -> None:
        first = CMIR(
            customer_identity=self.customer_identity,
            target_customer_material_ref=self.material_ref,
            brand="FirstVersion",
        )
        first_id = self.repository.supersede_and_insert(
            customer_identity=self.customer_identity,
            target_customer_material_ref=self.material_ref,
            merged=first,
            expected_current_id=None,
        )

        second = CMIR(
            customer_identity=self.customer_identity,
            target_customer_material_ref=self.material_ref,
            brand="SecondVersion",
        )
        second_id = self.repository.supersede_and_insert(
            customer_identity=self.customer_identity,
            target_customer_material_ref=self.material_ref,
            merged=second,
            expected_current_id=first_id,
        )

        current = self.repository.get_current(self.customer_identity, self.material_ref)
        self.assertEqual(current["id"], second_id)
        self.assertEqual(current["brand"], "SecondVersion")

    def test_supersede_and_insert_raises_conflict_on_stale_expected_id(self) -> None:
        cmir = CMIR(
            customer_identity=self.customer_identity,
            target_customer_material_ref=self.material_ref,
            brand="OnlyVersion",
        )
        self.repository.supersede_and_insert(
            customer_identity=self.customer_identity,
            target_customer_material_ref=self.material_ref,
            merged=cmir,
            expected_current_id=None,
        )

        with self.assertRaises(CMIRVersionConflict):
            self.repository.supersede_and_insert(
                customer_identity=self.customer_identity,
                target_customer_material_ref=self.material_ref,
                merged=cmir,
                expected_current_id=None,  # stale: a current row already exists now
            )


if __name__ == "__main__":
    unittest.main()
