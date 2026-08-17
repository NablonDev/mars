from __future__ import annotations

import copy
import unittest

from app.core.exceptions import ServiceError
from app.services.cmir_run_service import CMIRRunService


class FakeWorkflowThreads:
    def __init__(self, *, status: str = "waiting_approval", stage: str = "AWAITING_APPROVAL") -> None:
        self.stage_row = {
            "batch_id": "batch_01",
            "agent_run_id": 1042,
            "thread_id": "thread_01J4A",
            "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
            "stage": stage,
            "status": status,
            "current_node": "review_extracted_cmir",
            "pending_action_id": 3001,
            "updated_at": "2026-07-30T10:30:00+00:00",
            "source_message_id": "msg-001",
            "sender": "customer@example.com",
            "subject": "CMIR Request 1",
        }
        self.snapshot_row = {
            "batch_id": "batch_01",
            "agent_run_id": 1042,
            "thread_id": "thread_01J4A",
            "email": {
                "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
                "sender": "customer@example.com",
                "subject": "CMIR Request 1",
                "source_message_id": "msg-001",
            },
            "stage": stage,
            "editable_fields": ["brand", "existing_cmir_ref"],
            "cmir": {
                "brand": "Brand A",
                "existing_cmir_ref": "",
                "status": "pending_human_action",
            },
            "history": [],
            "updated_at": "2026-07-30T10:30:00+00:00",
        }

    def list_threads(self, **kwargs):
        return [], None

    def get_stage(self, thread_id):
        row = copy.deepcopy(self.stage_row)
        row["thread_id"] = thread_id
        return row

    def get_snapshot(self, thread_id):
        row = copy.deepcopy(self.snapshot_row)
        row["thread_id"] = thread_id
        return row

    def update_status(self, thread_id, **kwargs):
        self.stage_row.update(
            {
                "stage": kwargs["stage"],
                "status": kwargs["status"],
                "current_node": kwargs.get("current_node"),
                "pending_action_id": kwargs.get("pending_action_id"),
                "updated_at": "2026-07-30T10:31:00+00:00",
            }
        )


class FakeAgentRuns:
    def list_batches(self, **kwargs):
        return [], None

    def list_agents(self, **kwargs):
        return [], None

    def update_status(self, *args, **kwargs):
        return None

    def start(self, **kwargs):
        return 1042


class FakePendingActions:
    def __init__(self, interrupt_type: str = "approval_required") -> None:
        self.open_action = {
            "id": 3001,
            "batch_id": "batch_01",
            "agent_run_id": 1042,
            "thread_id": "thread_01J4A",
            "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
            "interrupt_type": interrupt_type,
            "payload": {"reason": interrupt_type},
            "state_snapshot": {"cmir": {"brand": "Brand A"}},
            "created_at": "2026-07-30T10:30:00+00:00",
        }

    def get_open_for_thread(self, thread_id):
        if self.open_action is None or self.open_action["thread_id"] != thread_id:
            return None
        return copy.deepcopy(self.open_action)


class FakeHITLState:
    def __init__(self, workflow_threads: FakeWorkflowThreads, pending_actions: FakePendingActions) -> None:
        self.workflow_threads = workflow_threads
        self.pending_actions = pending_actions
        self.calls = []
        self.fail = False
        self.next_pending_id = 4000

    def apply_human_action(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.fail:
            raise RuntimeError("transaction failed")

        next_pending_id = None
        if kwargs.get("next_pending_interrupt_type") is not None:
            next_pending_id = self.next_pending_id
            self.next_pending_id += 1
            self.pending_actions.open_action = {
                "id": next_pending_id,
                "batch_id": kwargs["batch_id"],
                "agent_run_id": kwargs["run_id"],
                "thread_id": kwargs["thread_id"],
                "email_id": kwargs["email_id"],
                "interrupt_type": kwargs["next_pending_interrupt_type"],
                "payload": kwargs["next_pending_payload"],
                "state_snapshot": kwargs["next_pending_state_snapshot"],
                "created_at": "2026-07-30T10:31:00+00:00",
            }
        else:
            self.pending_actions.open_action = None

        self.workflow_threads.stage_row.update(
            {
                "status": kwargs["next_status"],
                "stage": kwargs["next_stage"],
                "current_node": kwargs.get("next_current_node"),
                "pending_action_id": next_pending_id,
                "updated_at": "2026-07-30T10:31:00+00:00",
            }
        )
        self.workflow_threads.snapshot_row.update(
            {
                "stage": kwargs["next_stage"],
                "updated_at": "2026-07-30T10:31:00+00:00",
            }
        )
        if kwargs.get("next_latest_snapshot") is not None:
            self.workflow_threads.snapshot_row["cmir"] = copy.deepcopy(
                kwargs["next_latest_snapshot"].get("cmir", {})
            )

        return next_pending_id


class FakeGraph:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or {}
        self.error = error
        self.invocations = []
        self.updated_states = []

    def invoke(self, value, config=None):
        self.invocations.append((copy.deepcopy(value), copy.deepcopy(config)))
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.result)

    def update_state(self, config, value):
        self.updated_states.append((copy.deepcopy(config), copy.deepcopy(value)))


class FakeCMIRRepository:
    def __init__(self, current=None) -> None:
        self.current = current
        self.lookups = []

    def get_current(self, customer_identity, target_customer_material_ref):
        self.lookups.append((customer_identity, target_customer_material_ref))
        return self.current


class ServiceRuleTests(unittest.TestCase):
    def _build_service(
        self,
        *,
        workflow_threads: FakeWorkflowThreads,
        pending_actions: FakePendingActions,
        graph: FakeGraph,
        hitl_state: FakeHITLState,
        cmir_repository: FakeCMIRRepository | None = None,
    ) -> CMIRRunService:
        return CMIRRunService(
            email_reader=None,
            graph=graph,
            agent_runs=FakeAgentRuns(),
            workflow_threads=workflow_threads,
            pending_human_actions=pending_actions,
            hitl_actions=None,
            hitl_state=hitl_state,
            cmir_repository=cmir_repository or FakeCMIRRepository(),
        )

    def test_list_runs_rejects_unknown_view(self) -> None:
        workflow_threads = FakeWorkflowThreads()
        pending_actions = FakePendingActions()
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=FakeGraph(),
            hitl_state=hitl_state,
        )

        with self.assertRaises(ServiceError) as raised:
            service.list_runs(view="senders")

        self.assertEqual(raised.exception.code, "VALIDATION_ERROR")
        self.assertEqual(raised.exception.status_code, 422)

    def test_update_draft_rejects_bad_field_name(self) -> None:
        workflow_threads = FakeWorkflowThreads()
        pending_actions = FakePendingActions()
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=FakeGraph(),
            hitl_state=hitl_state,
        )

        with self.assertRaises(ServiceError) as raised:
            service.update_draft(
                "thread_01J4A",
                actor="reviewer@company.com",
                fields={"not_a_cmir_field": "x"},
                expected_updated_at="2026-07-30T10:30:00+00:00",
            )

        self.assertEqual(raised.exception.code, "VALIDATION_ERROR")

    def test_update_draft_rejects_stale_timestamp(self) -> None:
        workflow_threads = FakeWorkflowThreads()
        pending_actions = FakePendingActions()
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=FakeGraph(),
            hitl_state=hitl_state,
        )

        with self.assertRaises(ServiceError) as raised:
            service.update_draft(
                "thread_01J4A",
                actor="reviewer@company.com",
                fields={"brand": "Brand B"},
                expected_updated_at="2026-07-30T10:29:00+00:00",
            )

        self.assertEqual(raised.exception.code, "THREAD_STALE")

    def test_reject_decision_requires_reason(self) -> None:
        workflow_threads = FakeWorkflowThreads()
        pending_actions = FakePendingActions()
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=FakeGraph(),
            hitl_state=hitl_state,
        )

        with self.assertRaises(ServiceError) as raised:
            service.submit_decision(
                "thread_01J4A",
                actor="reviewer@company.com",
                decision="reject",
                reason="",
                expected_updated_at="2026-07-30T10:30:00+00:00",
            )

        self.assertEqual(raised.exception.code, "VALIDATION_ERROR")

    def test_missing_fields_resume_failure_keeps_pending_action_open(self) -> None:
        workflow_threads = FakeWorkflowThreads(
            status="waiting_missing_fields",
            stage="AWAITING_MISSING_FIELDS",
        )
        pending_actions = FakePendingActions(interrupt_type="missing_mandatory_fields")
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        graph = FakeGraph(error=RuntimeError("checkpoint missing"))
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=graph,
            hitl_state=hitl_state,
        )

        with self.assertRaises(ServiceError) as raised:
            service.submit_missing_fields(
                "thread_01J4A",
                actor="reviewer@company.com",
                fields={"existing_cmir_ref": "CMIR-1"},
                expected_updated_at="2026-07-30T10:30:00+00:00",
            )

        self.assertEqual(raised.exception.code, "WORKFLOW_RESUME_FAILED")
        self.assertEqual(pending_actions.open_action["id"], 3001)
        self.assertEqual(len(hitl_state.calls), 0)
        self.assertEqual(workflow_threads.stage_row["status"], "waiting_missing_fields")

    def test_missing_fields_success_rotates_to_approval_atomically(self) -> None:
        workflow_threads = FakeWorkflowThreads(
            status="waiting_missing_fields",
            stage="AWAITING_MISSING_FIELDS",
        )
        pending_actions = FakePendingActions(interrupt_type="missing_mandatory_fields")
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        graph = FakeGraph(
            result={
                "__interrupt__": [
                    type("Interrupt", (), {"value": {"reason": "approval_required", "email_id": "9c76", "cmir": {"brand": "Brand A", "existing_cmir_ref": "CMIR-1", "status": "pending_human_action"}}})()
                ],
                "batch_id": "batch_01",
                "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
                "thread_id": "thread_01J4A",
                "cmir": {"brand": "Brand A", "existing_cmir_ref": "CMIR-1", "status": "pending_human_action"},
            }
        )
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=graph,
            hitl_state=hitl_state,
        )

        response = service.submit_missing_fields(
            "thread_01J4A",
            actor="reviewer@company.com",
            fields={"existing_cmir_ref": "CMIR-1"},
            expected_updated_at="2026-07-30T10:30:00+00:00",
        )

        self.assertEqual(response["status"], "waiting_approval")
        self.assertEqual(response["stage"], "AWAITING_APPROVAL")
        self.assertEqual(pending_actions.open_action["interrupt_type"], "approval_required")
        self.assertEqual(hitl_state.calls[0]["pending_action_id"], 3001)

    def test_submit_decision_conflict_closes_thread_and_raises_version_conflict(self) -> None:
        workflow_threads = FakeWorkflowThreads()
        pending_actions = FakePendingActions()
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        graph = FakeGraph(
            result={
                "decision": "approve",
                "cmir_write_result": "conflict",
                "cmir": {"customer_identity": "Acme Manufacturing Ltd"},
            }
        )
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=graph,
            hitl_state=hitl_state,
        )

        with self.assertRaises(ServiceError) as raised:
            service.submit_decision(
                "thread_01J4A",
                actor="reviewer@company.com",
                decision="approve",
                expected_updated_at="2026-07-30T10:30:00+00:00",
            )

        self.assertEqual(raised.exception.code, "CMIR_VERSION_CONFLICT")
        self.assertEqual(raised.exception.status_code, 409)
        # The thread must still close out consistently (mirrors approve/reject),
        # not be left dangling just because the API call raises.
        self.assertEqual(hitl_state.calls[0]["next_status"], "completed_conflict")
        self.assertEqual(workflow_threads.stage_row["status"], "completed_conflict")

    def test_update_draft_merges_against_freshly_fetched_active_record(self) -> None:
        workflow_threads = FakeWorkflowThreads()
        pending_actions = FakePendingActions()
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        graph = FakeGraph()
        cmir_repository = FakeCMIRRepository(
            current={
                "id": 42,
                "customer_identity": "Acme Manufacturing Ltd",
                "target_customer_material_ref": "ACME-PE200-STD",
                "site": "Site 12",
            }
        )
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=graph,
            hitl_state=hitl_state,
            cmir_repository=cmir_repository,
        )

        service.update_draft(
            "thread_01J4A",
            actor="reviewer@company.com",
            fields={
                "customer_identity": "Acme Manufacturing Ltd",
                "target_customer_material_ref": "ACME-PE200-STD",
            },
            expected_updated_at="2026-07-30T10:30:00+00:00",
        )

        self.assertEqual(cmir_repository.lookups, [("Acme Manufacturing Ltd", "ACME-PE200-STD")])
        _, pushed_state = graph.updated_states[-1]
        self.assertEqual(pushed_state["cmir_version_token"], 42)
        self.assertEqual(pushed_state["existing_cmir"], cmir_repository.current)
        # "site" was never in the reviewer's edit or the pre-existing draft snapshot,
        # so it must have carried forward from the freshly-fetched active record.
        self.assertEqual(pushed_state["cmir"]["site"], "Site 12")

    def test_decision_resume_failure_keeps_pending_action_open(self) -> None:
        workflow_threads = FakeWorkflowThreads()
        pending_actions = FakePendingActions()
        hitl_state = FakeHITLState(workflow_threads, pending_actions)
        graph = FakeGraph(error=RuntimeError("checkpoint missing"))
        service = self._build_service(
            workflow_threads=workflow_threads,
            pending_actions=pending_actions,
            graph=graph,
            hitl_state=hitl_state,
        )

        with self.assertRaises(ServiceError) as raised:
            service.submit_decision(
                "thread_01J4A",
                actor="reviewer@company.com",
                decision="approve",
                expected_updated_at="2026-07-30T10:30:00+00:00",
            )

        self.assertEqual(raised.exception.code, "WORKFLOW_RESUME_FAILED")
        self.assertEqual(pending_actions.open_action["id"], 3001)
        self.assertEqual(len(hitl_state.calls), 0)


if __name__ == "__main__":
    unittest.main()
