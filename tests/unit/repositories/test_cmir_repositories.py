from __future__ import annotations

import unittest
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError

from app.models.cmir import CMIRRecordORM
from app.models.email import EmailEventORM
from app.models.observability import (
    HITLActionORM,
    PendingHumanActionORM,
    WorkflowThreadORM,
)
from app.repositories.cmir import CMIRVersionConflict, PostgresCMIRRepository
from app.repositories.email import PostgresEmailRepository
from app.repositories.observability import (
    PostgresHITLActionRepository,
    PostgresHITLStateRepository,
    PostgresPendingHumanActionRepository,
    PostgresWorkflowThreadRepository,
)
from app.schemas.cmir import CMIR, EmailMessage, PendingHumanAction, WorkflowThread


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
        self.committed = False

    def add(self, record) -> None:
        self.added.append(record)

    def flush(self) -> None:
        for index, record in enumerate(self.added, start=1):
            if getattr(record, "id", None) is None:
                record.id = index

    def execute(self, statement):
        self.executed.append(statement)
        return FakeResult()

    def scalar(self, statement):
        self.executed.append(statement)
        return self.scalar_row

    def scalars(self, statement):
        self.executed.append(statement)
        return FakeScalarResult(self.scalar_rows)


class FailingFlushSession(FakeSession):
    """FakeSession whose Nth flush() raises IntegrityError, simulating the database's
    partial unique index rejecting a concurrent insert -- used to verify
    supersede_and_insert translates that into CMIRVersionConflict rather than letting
    the raw driver exception escape.
    """

    def __init__(self, *args, fail_on_flush_number: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fail_on_flush_number = fail_on_flush_number
        self._flush_count = 0

    def flush(self) -> None:
        self._flush_count += 1
        if self._flush_count == self._fail_on_flush_number:
            raise IntegrityError(
                "INSERT INTO cmir_records ...",
                {},
                Exception("duplicate key value violates unique constraint"),
            )
        super().flush()


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self.session_obj = session

    @contextmanager
    def session(self):
        yield self.session_obj
        self.session_obj.committed = True


class SQLAlchemyRepositoryTests(unittest.TestCase):
    def test_email_repository_adds_email_event_orm(self) -> None:
        session = FakeSession()
        repository = PostgresEmailRepository(FakeDatabase(session))

        email_id = repository.save(
            EmailMessage(
                imap_id="1",
                sender="customer@example.com",
                subject="CMIR Request",
                body="body",
                source_message_id="msg-001",
            )
        )

        self.assertEqual(email_id, 1)
        self.assertIsInstance(session.added[0], EmailEventORM)
        self.assertEqual(session.added[0].source_message_id, "msg-001")

    def test_workflow_thread_repository_adds_thread_orm(self) -> None:
        session = FakeSession()
        repository = PostgresWorkflowThreadRepository(FakeDatabase(session))

        thread_id = repository.create(
            WorkflowThread(
                thread_id="thread_01J4A",
                agent_run_id="00000000-0000-0000-0000-000000001042",
                batch_id="batch_01",
                email_id="9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
                source_message_id="msg-001",
                sender="customer@example.com",
                subject="CMIR Request 1",
                latest_snapshot={"cmir": {"brand": "Brand A"}},
            )
        )

        self.assertEqual(thread_id, "thread_01J4A")
        self.assertIsInstance(session.added[0], WorkflowThreadORM)
        self.assertEqual(session.added[0].batch_id, "batch_01")
        self.assertEqual(session.added[0].thread_id, "thread_01J4A")
        self.assertEqual(session.added[0].latest_snapshot["cmir"]["brand"], "Brand A")

    def test_workflow_thread_get_snapshot_exposes_existing_cmir_and_diff(self) -> None:
        thread = WorkflowThreadORM(
            thread_id="thread_01J4A",
            batch_id="batch_01",
            agent_run_id="00000000-0000-0000-0000-000000001042",
            email_id="9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
            sender="customer@example.com",
            subject="CMIR Request 1",
            stage="AWAITING_APPROVAL",
            latest_snapshot={
                "cmir": {"brand": "AcmePlast"},
                "existing_cmir": {"id": 42, "brand": "OldBrand"},
                "diff": {"brand": {"from": "OldBrand", "to": "AcmePlast"}},
            },
        )
        session = FakeSession(scalar_row=thread, scalar_rows=[])
        repository = PostgresWorkflowThreadRepository(FakeDatabase(session))

        snapshot = repository.get_snapshot("thread_01J4A")

        self.assertEqual(snapshot["cmir"], {"brand": "AcmePlast"})
        self.assertEqual(snapshot["existing_cmir"], {"id": 42, "brand": "OldBrand"})
        self.assertEqual(snapshot["diff"], {"brand": {"from": "OldBrand", "to": "AcmePlast"}})

    def test_workflow_thread_get_snapshot_defaults_when_snapshot_predates_scd2(self) -> None:
        # A row written before this change has no existing_cmir/diff keys at all --
        # get_snapshot must not KeyError on it.
        thread = WorkflowThreadORM(
            thread_id="thread_legacy",
            batch_id="batch_01",
            agent_run_id="00000000-0000-0000-0000-000000001042",
            email_id="9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
            sender="customer@example.com",
            subject="CMIR Request 1",
            stage="AWAITING_APPROVAL",
            latest_snapshot={"cmir": {"brand": "AcmePlast"}},
        )
        session = FakeSession(scalar_row=thread, scalar_rows=[])
        repository = PostgresWorkflowThreadRepository(FakeDatabase(session))

        snapshot = repository.get_snapshot("thread_legacy")

        self.assertIsNone(snapshot["existing_cmir"])
        self.assertEqual(snapshot["diff"], {})

    def test_pending_action_repository_adds_open_action_orm(self) -> None:
        session = FakeSession()
        repository = PostgresPendingHumanActionRepository(FakeDatabase(session))

        action_id = repository.create_open(
            PendingHumanAction(
                agent_run_id="00000000-0000-0000-0000-000000001042",
                batch_id="batch_01",
                thread_id="thread_01J4A",
                email_id="9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
                interrupt_type="approval_required",
                payload={"cmir": {"brand": "Brand A"}},
                state_snapshot={"cmir": {"brand": "Brand A"}},
            )
        )

        self.assertEqual(action_id, 1)
        self.assertIsInstance(session.added[0], PendingHumanActionORM)
        self.assertEqual(session.added[0].batch_id, "batch_01")
        self.assertEqual(session.added[0].status, "open")

    def test_hitl_action_repository_adds_thread_aware_audit_orm(self) -> None:
        session = FakeSession()
        repository = PostgresHITLActionRepository(FakeDatabase(session))

        repository.log(
            run_id="00000000-0000-0000-0000-000000001042",
            email_id="9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
            interrupt_type="approval_required",
            question={"reason": "approval_required"},
            answer={"decision": "approve"},
            actor="reviewer@company.com",
            decision="approve",
            batch_id="batch_01",
            thread_id="thread_01J4A",
            action_type="decision",
            field_changes={"brand": {"from": "", "to": "Brand A"}},
        )

        self.assertIsInstance(session.added[0], HITLActionORM)
        self.assertEqual(session.added[0].batch_id, "batch_01")
        self.assertEqual(session.added[0].thread_id, "thread_01J4A")
        self.assertEqual(session.added[0].field_changes["brand"]["to"], "Brand A")

    def test_hitl_state_repository_applies_human_action_transactionally(self) -> None:
        session = FakeSession()
        repository = PostgresHITLStateRepository(FakeDatabase(session))

        new_action_id = repository.apply_human_action(
            run_id="00000000-0000-0000-0000-000000001042",
            batch_id="batch_01",
            thread_id="thread_01J4A",
            email_id="9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
            pending_action_id="00000000-0000-0000-0000-000000003001",
            interrupt_type="missing_mandatory_fields",
            question={"reason": "missing_mandatory_fields"},
            answer={"existing_cmir_ref": "CMIR-1"},
            actor="reviewer@company.com",
            action_type="field_update",
            field_changes={"existing_cmir_ref": {"from": "", "to": "CMIR-1"}},
            next_status="waiting_approval",
            next_stage="AWAITING_APPROVAL",
            next_current_node="review_extracted_cmir",
            next_cmir_status="pending_human_action",
            next_latest_snapshot={"cmir": {"existing_cmir_ref": "CMIR-1"}},
            next_pending_interrupt_type="approval_required",
            next_pending_payload={"reason": "approval_required"},
            next_pending_state_snapshot={"cmir": {"existing_cmir_ref": "CMIR-1"}},
        )

        self.assertEqual(new_action_id, 2)
        self.assertIsInstance(session.added[0], HITLActionORM)
        self.assertIsInstance(session.added[1], PendingHumanActionORM)

    def test_get_current_returns_none_when_no_current_row(self) -> None:
        session = FakeSession(scalar_row=None)
        repository = PostgresCMIRRepository(FakeDatabase(session))

        self.assertIsNone(repository.get_current("Acme Manufacturing Ltd", "ACME-PE200-STD"))

    def test_get_current_returns_full_content_field_dict(self) -> None:
        session = FakeSession(
            scalar_row=CMIRRecordORM(
                id=42,
                customer_identity="Acme Manufacturing Ltd",
                material_identity="Polyethylene Resin PE-200",
                target_customer_material_ref="ACME-PE200-STD",
                brand="AcmePlast",
            )
        )
        repository = PostgresCMIRRepository(FakeDatabase(session))

        current = repository.get_current("Acme Manufacturing Ltd", "ACME-PE200-STD")

        self.assertEqual(current["id"], 42)
        self.assertEqual(current["brand"], "AcmePlast")
        # Every CMIR_CONTENT_FIELDS key must be present -- this is the exact shape
        # merge_with_active's `existing` argument depends on.
        for field_name in (
            "sender_type",
            "customer_identity",
            "material_identity",
            "intent_phrase",
            "existing_cmir_ref",
            "brand",
            "site",
            "target_grd_code",
            "target_customer_material_ref",
            "effective_date",
            "reason",
        ):
            self.assertIn(field_name, current)

    def test_supersede_and_insert_create_path_when_no_current_record_exists(self) -> None:
        session = FakeSession(scalar_row=None)
        repository = PostgresCMIRRepository(FakeDatabase(session))

        new_id = repository.supersede_and_insert(
            customer_identity="Acme Manufacturing Ltd",
            target_customer_material_ref="ACME-PE200-STD",
            merged=CMIR(customer_identity="Acme Manufacturing Ltd", brand="AcmePlast"),
            expected_current_id=None,
        )

        self.assertEqual(new_id, 1)
        self.assertEqual(len(session.added), 1)
        self.assertIsInstance(session.added[0], CMIRRecordORM)
        self.assertTrue(session.added[0].is_current)
        self.assertIsNone(session.added[0].email_id)

    def test_supersede_and_insert_update_path_retires_old_record(self) -> None:
        old_record = CMIRRecordORM(
            id=42,
            customer_identity="Acme Manufacturing Ltd",
            target_customer_material_ref="ACME-PE200-STD",
            is_current=True,
        )
        session = FakeSession(scalar_row=old_record)
        repository = PostgresCMIRRepository(FakeDatabase(session))

        new_id = repository.supersede_and_insert(
            customer_identity="Acme Manufacturing Ltd",
            target_customer_material_ref="ACME-PE200-STD",
            merged=CMIR(customer_identity="Acme Manufacturing Ltd", brand="AcmePlast Europe"),
            expected_current_id=42,
        )

        self.assertEqual(len(session.added), 1)
        self.assertFalse(old_record.is_current)
        self.assertIsNotNone(old_record.valid_to)
        self.assertEqual(old_record.superseded_by_id, new_id)

    def test_supersede_and_insert_raises_conflict_when_current_id_mismatches(self) -> None:
        session = FakeSession(scalar_row=CMIRRecordORM(id=42, is_current=True))
        repository = PostgresCMIRRepository(FakeDatabase(session))

        with self.assertRaises(CMIRVersionConflict):
            repository.supersede_and_insert(
                customer_identity="Acme Manufacturing Ltd",
                target_customer_material_ref="ACME-PE200-STD",
                merged=CMIR(customer_identity="Acme Manufacturing Ltd"),
                expected_current_id=99,  # stale -- someone else already superseded id 42
            )

        # No insert should even be attempted once the id comparison fails.
        self.assertEqual(session.added, [])

    def test_supersede_and_insert_translates_integrity_error_to_version_conflict(self) -> None:
        # No existing current row -> exactly one flush() call (the new insert), so
        # fail_on_flush_number=1 simulates the database rejecting that insert.
        session = FailingFlushSession(scalar_row=None, fail_on_flush_number=1)
        repository = PostgresCMIRRepository(FakeDatabase(session))

        with self.assertRaises(CMIRVersionConflict):
            repository.supersede_and_insert(
                customer_identity="Acme Manufacturing Ltd",
                target_customer_material_ref="ACME-PE200-STD",
                merged=CMIR(customer_identity="Acme Manufacturing Ltd"),
                expected_current_id=None,
            )


if __name__ == "__main__":
    unittest.main()
