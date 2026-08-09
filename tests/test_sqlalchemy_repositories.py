from __future__ import annotations

import unittest
from contextlib import contextmanager

from cmir_agent.domain.models import EmailMessage, PendingHumanAction, WorkflowThread
from cmir_agent.domain.models import CMIR
from cmir_agent.infrastructure.orm_models import (
    CMIRRecordORM,
    EmailEventORM,
    HITLActionORM,
    PendingHumanActionORM,
    WorkflowThreadORM,
)
from cmir_agent.infrastructure.postgres_observability import (
    PostgresHITLActionRepository,
    PostgresHITLStateRepository,
    PostgresPendingHumanActionRepository,
    PostgresWorkflowThreadRepository,
)
from cmir_agent.infrastructure.postgres_repositories import PostgresCMIRRepository, PostgresEmailRepository


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
                agent_run_id=1042,
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

    def test_pending_action_repository_adds_open_action_orm(self) -> None:
        session = FakeSession()
        repository = PostgresPendingHumanActionRepository(FakeDatabase(session))

        action_id = repository.create_open(
            PendingHumanAction(
                agent_run_id=1042,
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
            run_id=1042,
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
            run_id=1042,
            batch_id="batch_01",
            thread_id="thread_01J4A",
            email_id="9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
            pending_action_id=3001,
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

    def test_cmir_repository_returns_existing_record_for_same_email(self) -> None:
        session = FakeSession(scalar_row=CMIRRecordORM(id=99, email_id="email-1"))
        repository = PostgresCMIRRepository(FakeDatabase(session))

        record_id = repository.save(
            "email-1",
            CMIR(
                sender_type="external",
                customer_identity="Customer",
                material_identity="Material",
                intent_phrase="Intent",
                existing_cmir_ref="CMIR-1",
                brand="Brand",
                site="Site",
                target_grd_code="GRD",
                target_customer_material_ref="REF",
            ),
        )

        self.assertEqual(record_id, 99)
        self.assertEqual(session.added, [])


if __name__ == "__main__":
    unittest.main()
