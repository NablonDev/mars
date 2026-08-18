from __future__ import annotations

import unittest
from contextlib import contextmanager

from app.models.cmir import CMIRRecordORM
from app.models.po_validation import MaterialMasterORM, PoLineErrorORM, PoLineORM
from app.repositories.cmir import CMIRVersionConflict, PostgresCMIRRepository
from app.repositories.po_validation import (
    PostgresMaterialMasterRepository,
    PostgresPoLineErrorRepository,
    PostgresPoLineRepository,
)
from app.schemas.po_validation import PoLine, PoLineError


class FakeScalarResult:
    def __init__(self, rows=None) -> None:
        self._rows = rows or []

    def all(self):
        return self._rows


class FakeResult:
    rowcount = 1


class FakeSession:
    def __init__(self, scalar_row=None, scalar_rows=None) -> None:
        self.added = []
        self.executed = []
        self.scalar_row = scalar_row
        self.scalar_rows = scalar_rows or []

    def add(self, record) -> None:
        self.added.append(record)

    def flush(self) -> None:
        for index, record in enumerate(self.added, start=1):
            if getattr(record, "id", None) is None:
                record.id = f"generated-{index}"

    def execute(self, statement):
        self.executed.append(statement)
        return FakeResult()

    def scalar(self, statement):
        self.executed.append(statement)
        return self.scalar_row

    def scalars(self, statement):
        self.executed.append(statement)
        return FakeScalarResult(self.scalar_rows)


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self.session_obj = session

    @contextmanager
    def session(self):
        yield self.session_obj
        self.session_obj.committed = True


class PoValidationRepositoryTests(unittest.TestCase):
    def test_po_line_repository_adds_po_line_orm(self) -> None:
        session = FakeSession()
        repository = PostgresPoLineRepository(FakeDatabase(session))

        po_line_id = repository.create(
            PoLine(
                batch_id="batch_po_01",
                po_number="PO-1",
                po_line_number="10",
                customer_id="CUST-1",
                customer_material_code="ACME-MAT-1",
                plant="1000",
                order_quantity=100,
                raw_payload={"po_number": "PO-1"},
            )
        )

        self.assertEqual(po_line_id, "generated-1")
        self.assertIsInstance(session.added[0], PoLineORM)
        self.assertEqual(session.added[0].plant, "1000")
        self.assertEqual(session.added[0].status, "NEW")

    def test_material_master_repository_maps_orm_row_to_domain_record(self) -> None:
        session = FakeSession(
            scalar_row=MaterialMasterORM(
                sap_material_number="MAT-1",
                plant="1000",
                available_quantity=40,
                follow_up_material_number="MAT-SUB",
            )
        )
        repository = PostgresMaterialMasterRepository(FakeDatabase(session))

        record = repository.find("MAT-1", "1000")

        self.assertIsNotNone(record)
        self.assertEqual(record.available_quantity, 40)
        self.assertEqual(record.follow_up_material_number, "MAT-SUB")

    def test_material_master_repository_returns_none_when_missing(self) -> None:
        session = FakeSession(scalar_row=None)
        repository = PostgresMaterialMasterRepository(FakeDatabase(session))

        self.assertIsNone(repository.find("MAT-UNKNOWN", "1000"))

    def test_po_line_error_repository_adds_po_line_error_orm(self) -> None:
        session = FakeSession()
        repository = PostgresPoLineErrorRepository(FakeDatabase(session))

        repository.log(
            PoLineError(
                po_line_id="po_line_1",
                error_type="LOOKUP_FAILURE",
                node_name="check_material_master",
                error_code="LookupError",
                error_message="no material_master row",
            )
        )

        self.assertIsInstance(session.added[0], PoLineErrorORM)
        self.assertEqual(session.added[0].error_type, "LOOKUP_FAILURE")
        self.assertEqual(session.added[0].node_name, "check_material_master")

    def test_cmir_repository_find_latest_filters_on_is_current(self) -> None:
        session = FakeSession(
            scalar_row=CMIRRecordORM(
                id=7,
                customer_identity="CUST-1",
                material_identity="MAT-1",
                target_customer_material_ref="ACME-MAT-1",
                is_current=True,
            )
        )
        repository = PostgresCMIRRepository(FakeDatabase(session))

        match = repository.find_latest_for_customer_material("CUST-1", "ACME-MAT-1")

        self.assertEqual(match["material_identity"], "MAT-1")
        statement = session.executed[0]
        self.assertIn("is_current", str(statement))

    def test_cmir_repository_find_latest_returns_none_when_no_current_row(self) -> None:
        session = FakeSession(scalar_row=None)
        repository = PostgresCMIRRepository(FakeDatabase(session))

        self.assertIsNone(repository.find_latest_for_customer_material("CUST-1", "ACME-MAT-1"))

    def test_cmir_repository_create_manual_mapping_has_no_email_id(self) -> None:
        # No current row exists (scalar_row=None) -- the precondition create_manual_mapping
        # is always called under, since PO Validation only reaches it after
        # validate_against_cmir found nothing.
        session = FakeSession(scalar_row=None)
        repository = PostgresCMIRRepository(FakeDatabase(session))

        repository.create_manual_mapping(
            customer_identity="CUST-1",
            material_identity="MAT-100",
            target_customer_material_ref="ACME-MAT-1",
            description="Legacy SKU",
        )

        self.assertIsInstance(session.added[0], CMIRRecordORM)
        self.assertIsNone(session.added[0].email_id)
        self.assertEqual(session.added[0].material_identity, "MAT-100")
        self.assertEqual(session.added[0].reason, "Legacy SKU")
        self.assertTrue(session.added[0].is_current)

    def test_cmir_repository_create_manual_mapping_raises_conflict_if_current_appeared(self) -> None:
        # A current row unexpectedly exists (a race between validate_against_cmir's
        # "not found" and this call) -- create_manual_mapping must not silently
        # create a second current row for the same entity.
        session = FakeSession(
            scalar_row=CMIRRecordORM(
                id=99,
                customer_identity="CUST-1",
                target_customer_material_ref="ACME-MAT-1",
                is_current=True,
            )
        )
        repository = PostgresCMIRRepository(FakeDatabase(session))

        with self.assertRaises(CMIRVersionConflict):
            repository.create_manual_mapping(
                customer_identity="CUST-1",
                material_identity="MAT-100",
                target_customer_material_ref="ACME-MAT-1",
            )


if __name__ == "__main__":
    unittest.main()
