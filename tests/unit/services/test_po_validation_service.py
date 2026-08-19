from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from app.core.exceptions import ServiceError
from app.schemas.po_validation import MaterialMasterRecord
from app.services.cmir_run_service import INTERRUPT_KEY
from app.services.po_validation_service import PoValidationService


class FakePoLines:
    def __init__(self) -> None:
        self.rows: dict = {}
        self._next_id = 1
        # Lets a test simulate "the graph's outcome node already set this status"
        # without needing a real graph run inside these service-level tests.
        self.forced_final_status: str | None = None

    def create(self, line):
        po_line_id = f"po_line_{self._next_id}"
        self._next_id += 1
        self.rows[po_line_id] = {
            "id": po_line_id,
            "batch_id": line.batch_id,
            "po_number": line.po_number,
            "po_line_number": line.po_line_number,
            "customer_id": line.customer_id,
            "customer_material_code": line.customer_material_code,
            "plant": line.plant,
            "order_quantity": line.order_quantity,
            "uom": line.uom,
            "status": line.status,
        }
        return po_line_id

    def get(self, po_line_id):
        row = self.rows.get(po_line_id)
        if row is None:
            return None
        row = copy.deepcopy(row)
        if self.forced_final_status is not None:
            row["status"] = self.forced_final_status
        return row

    def update_status(self, po_line_id, status):
        if po_line_id in self.rows:
            self.rows[po_line_id]["status"] = status

    def list_by_status(self, **kwargs):
        return [], None


class FakeMaterialMaster:
    def __init__(self, records=None) -> None:
        self.records = records or {}

    def find(self, sap_material_number, plant):
        return self.records.get((sap_material_number, plant))


class FakePoLineErrors:
    def __init__(self) -> None:
        self.logged = []

    def log(self, error):
        self.logged.append(error)
        return 1

    def list_for_po_line(self, po_line_id):
        return []


class FakeAgentRuns:
    def __init__(self) -> None:
        self.started = []
        self.updates = []

    def start(self, **kwargs):
        self.started.append(kwargs)
        return 9001

    def update_status(self, *args, **kwargs):
        self.updates.append((args, kwargs))


class FakeWorkflowThreads:
    def __init__(self) -> None:
        self.created = None
        self.rows: dict = {}

    def create(self, thread):
        self.created = thread
        self.rows[thread.thread_id] = {
            "batch_id": thread.batch_id,
            "agent_run_id": thread.agent_run_id,
            "thread_id": thread.thread_id,
            "email_id": thread.email_id,
            "po_line_id": thread.po_line_id,
            "stage": thread.stage,
            "status": thread.status,
            "current_node": thread.current_node,
            "pending_action_id": thread.pending_action_id,
            "updated_at": "2026-08-09T10:00:00+00:00",
        }
        return thread.thread_id

    def update_status(self, thread_id, **kwargs):
        row = self.rows[thread_id]
        row.update(
            {
                "status": kwargs["status"],
                "stage": kwargs["stage"],
                "current_node": kwargs.get("current_node"),
                "pending_action_id": kwargs.get("pending_action_id"),
                "updated_at": "2026-08-09T10:01:00+00:00",
            }
        )

    def get_stage(self, thread_id):
        row = self.rows.get(thread_id)
        return copy.deepcopy(row) if row else None


class FakePendingActions:
    def __init__(self) -> None:
        self.open_action = None

    def create_open(self, action):
        self.open_action = {
            "id": "00000000-0000-0000-0000-000000005001",
            "batch_id": action.batch_id,
            "agent_run_id": action.agent_run_id,
            "thread_id": action.thread_id,
            "email_id": action.email_id,
            "po_line_id": action.po_line_id,
            "interrupt_type": action.interrupt_type,
            "payload": action.payload,
            "state_snapshot": action.state_snapshot,
            "created_at": "2026-08-09T10:00:00+00:00",
        }
        return self.open_action["id"]

    def get_open_for_thread(self, thread_id):
        if self.open_action is None or self.open_action["thread_id"] != thread_id:
            return None
        return copy.deepcopy(self.open_action)


class FakeHITLActions:
    def list_for_thread(self, thread_id):
        return []


class FakeHITLState:
    def __init__(self, workflow_threads: FakeWorkflowThreads, pending_actions: FakePendingActions) -> None:
        self.workflow_threads = workflow_threads
        self.pending_actions = pending_actions
        self.fail = False
        self._next_id = 6000
        self.calls = []

    def apply_human_action(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.fail:
            raise RuntimeError("transaction failed")

        thread_id = kwargs["thread_id"]
        next_pending_id = None
        if kwargs.get("next_pending_interrupt_type") is not None:
            next_pending_id = self._next_id
            self._next_id += 1
            self.pending_actions.open_action = {
                "id": next_pending_id,
                "batch_id": kwargs["batch_id"],
                "agent_run_id": kwargs["run_id"],
                "thread_id": thread_id,
                "email_id": kwargs.get("email_id"),
                "po_line_id": kwargs.get("po_line_id"),
                "interrupt_type": kwargs["next_pending_interrupt_type"],
                "payload": kwargs["next_pending_payload"],
                "state_snapshot": kwargs["next_pending_state_snapshot"],
                "created_at": "2026-08-09T10:02:00+00:00",
            }
        else:
            self.pending_actions.open_action = None

        self.workflow_threads.rows[thread_id].update(
            {
                "status": kwargs["next_status"],
                "stage": kwargs["next_stage"],
                "current_node": None if kwargs.get("completed") else kwargs.get("next_current_node"),
                "pending_action_id": next_pending_id,
                "updated_at": "2026-08-09T10:02:00+00:00",
            }
        )
        return next_pending_id


class FakeGraph:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or {}
        self.error = error
        self.invocations = []

    def invoke(self, value, config=None):
        self.invocations.append((copy.deepcopy(value), copy.deepcopy(config)))
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.result)


def _line_payload(**overrides):
    payload = {
        "po_number": "PO-2026-0001",
        "po_line_number": "10",
        "customer_id": "CUST-1",
        "customer_material_code": "ACME-MAT-1",
        "plant": "1000",
        "order_quantity": 100,
    }
    payload.update(overrides)
    return payload


def _qty_mismatch_interrupt(po_line_id: str, suggested: str = "MAT-SUB-1"):
    return {
        INTERRUPT_KEY: [
            SimpleNamespace(
                value={
                    "reason": "qty_mismatch_decision",
                    "po_line_id": po_line_id,
                    "po_number": "PO-2026-0001",
                    "po_line_number": "10",
                    "customer_id": "CUST-1",
                    "candidate": {
                        "sap_material_number": "MAT-1",
                        "plant": "1000",
                        "available_quantity": 40,
                        "shortfall": 60,
                        "suggested_substitute_material_code": suggested,
                    },
                }
            )
        ]
    }


class PoValidationServiceTests(unittest.TestCase):
    def _build(self, *, graph, po_lines=None, material_master=None):
        self.po_lines = po_lines or FakePoLines()
        self.material_master = material_master or FakeMaterialMaster()
        self.po_line_errors = FakePoLineErrors()
        self.agent_runs = FakeAgentRuns()
        self.workflow_threads = FakeWorkflowThreads()
        self.pending_actions = FakePendingActions()
        self.hitl_actions = FakeHITLActions()
        self.hitl_state = FakeHITLState(self.workflow_threads, self.pending_actions)
        return PoValidationService(
            graph=graph,
            po_lines=self.po_lines,
            material_master=self.material_master,
            po_line_errors=self.po_line_errors,
            agent_runs=self.agent_runs,
            workflow_threads=self.workflow_threads,
            pending_human_actions=self.pending_actions,
            hitl_actions=self.hitl_actions,
            hitl_state=self.hitl_state,
        )

    def test_touchless_ready_line_creates_no_thread(self) -> None:
        po_lines = FakePoLines()
        po_lines.forced_final_status = "READY_FOR_SO_CREATION"
        service = self._build(graph=FakeGraph(result={}), po_lines=po_lines)

        result = service.ingest_po_lines([_line_payload()])

        line = result["lines"][0]
        self.assertIsNone(line["thread_id"])
        self.assertEqual(line["status"], "READY_FOR_SO_CREATION")
        self.assertIsNone(self.workflow_threads.created)

    def test_qty_mismatch_interrupt_creates_thread_and_pending_action(self) -> None:
        po_lines = FakePoLines()
        po_line_id = "po_line_pending"
        graph = FakeGraph(result=_qty_mismatch_interrupt(po_line_id))
        service = self._build(graph=graph, po_lines=po_lines)
        # Bypass create() bookkeeping details; only po_line_id needs to match the interrupt payload.
        po_lines.rows[po_line_id] = {
            "id": po_line_id,
            "batch_id": "batch_x",
            "po_number": "PO-2026-0001",
            "po_line_number": "10",
            "customer_id": "CUST-1",
            "customer_material_code": "ACME-MAT-1",
            "plant": "1000",
            "order_quantity": 100,
            "uom": None,
            "status": "NEW",
        }
        po_lines.create = lambda line: po_line_id  # force the known id for this test

        result = service.ingest_po_lines([_line_payload()])

        line = result["lines"][0]
        self.assertIsNotNone(line["thread_id"])
        self.assertEqual(line["status"], "AWAITING_DECISION")
        self.assertIsNotNone(self.workflow_threads.created)
        self.assertEqual(self.workflow_threads.created.po_line_id, po_line_id)
        self.assertIsNotNone(self.pending_actions.open_action)
        self.assertEqual(self.pending_actions.open_action["interrupt_type"], "qty_mismatch_decision")
        self.assertEqual(po_lines.rows[po_line_id]["status"], "AWAITING_DECISION")

    def test_submit_qty_mismatch_decision_rejects_wrong_stage(self) -> None:
        service = self._build(graph=FakeGraph())
        self.workflow_threads.rows["thread_po_1"] = {
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "stage": "AWAITING_MANUAL_CMIR_ENTRY",
            "status": "waiting_manual_cmir_entry",
            "current_node": "human_manual_cmir_entry",
            "pending_action_id": "00000000-0000-0000-0000-000000005001",
            "updated_at": "2026-08-09T10:00:00+00:00",
        }

        with self.assertRaises(ServiceError) as raised:
            service.submit_qty_mismatch_decision(
                "thread_po_1",
                actor="csr@company.com",
                decision="proceed_anyway",
                expected_updated_at="2026-08-09T10:00:00+00:00",
            )

        self.assertEqual(raised.exception.code, "THREAD_NOT_WAITING")

    def test_submit_qty_mismatch_decision_stale_timestamp(self) -> None:
        service = self._build(graph=FakeGraph())
        self.workflow_threads.rows["thread_po_1"] = {
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "stage": "AWAITING_QTY_MISMATCH_DECISION",
            "status": "waiting_qty_mismatch_decision",
            "current_node": "human_qty_mismatch_decision",
            "pending_action_id": "00000000-0000-0000-0000-000000005001",
            "updated_at": "2026-08-09T10:00:00+00:00",
        }

        with self.assertRaises(ServiceError) as raised:
            service.submit_qty_mismatch_decision(
                "thread_po_1",
                actor="csr@company.com",
                decision="proceed_anyway",
                expected_updated_at="2020-01-01T00:00:00+00:00",
            )

        self.assertEqual(raised.exception.code, "THREAD_STALE")

    def test_submit_qty_mismatch_use_substitute_rejects_unknown_material(self) -> None:
        service = self._build(graph=FakeGraph())
        self.po_lines.rows["po_line_1"] = {
            "id": "po_line_1",
            "batch_id": "batch_x",
            "plant": "1000",
            "po_number": "PO-1",
            "po_line_number": "10",
            "customer_id": "CUST-1",
            "customer_material_code": "MAT-REF",
            "order_quantity": 100,
            "uom": None,
            "status": "AWAITING_DECISION",
        }
        self.workflow_threads.rows["thread_po_1"] = {
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "stage": "AWAITING_QTY_MISMATCH_DECISION",
            "status": "waiting_qty_mismatch_decision",
            "current_node": "human_qty_mismatch_decision",
            "pending_action_id": "00000000-0000-0000-0000-000000005001",
            "updated_at": "2026-08-09T10:00:00+00:00",
        }
        self.pending_actions.open_action = {
            "id": "00000000-0000-0000-0000-000000005001",
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "interrupt_type": "qty_mismatch_decision",
            "payload": {"candidate": {"suggested_substitute_material_code": None}},
            "state_snapshot": {},
            "created_at": "2026-08-09T10:00:00+00:00",
        }

        with self.assertRaises(ServiceError) as raised:
            service.submit_qty_mismatch_decision(
                "thread_po_1",
                actor="csr@company.com",
                decision="use_substitute",
                substitute_material_code="MAT-UNKNOWN",
                expected_updated_at="2026-08-09T10:00:00+00:00",
            )

        self.assertEqual(raised.exception.code, "MATERIAL_NOT_FOUND")
        # The graph must never be invoked with an unvalidated reviewer-submitted material.
        self.assertEqual(len(self._graph_of(service).invocations), 0)

    def test_submit_manual_cmir_entry_validates_material_before_resume(self) -> None:
        service = self._build(graph=FakeGraph())
        self.po_lines.rows["po_line_1"] = {
            "id": "po_line_1",
            "batch_id": "batch_x",
            "plant": "1000",
            "po_number": "PO-1",
            "po_line_number": "10",
            "customer_id": "CUST-1",
            "customer_material_code": "MAT-REF",
            "order_quantity": 100,
            "uom": None,
            "status": "AWAITING_DECISION",
        }
        self.workflow_threads.rows["thread_po_1"] = {
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "stage": "AWAITING_MANUAL_CMIR_ENTRY",
            "status": "waiting_manual_cmir_entry",
            "current_node": "human_manual_cmir_entry",
            "pending_action_id": "00000000-0000-0000-0000-000000005001",
            "updated_at": "2026-08-09T10:00:00+00:00",
        }

        with self.assertRaises(ServiceError) as raised:
            service.submit_manual_cmir_entry(
                "thread_po_1",
                actor="csr@company.com",
                sap_material_number="MAT-UNKNOWN",
                expected_updated_at="2026-08-09T10:00:00+00:00",
            )

        self.assertEqual(raised.exception.code, "MATERIAL_NOT_FOUND")
        self.assertEqual(len(self._graph_of(service).invocations), 0)

    def test_manual_cmir_entry_resume_failure_keeps_pending_action_open(self) -> None:
        material_master = FakeMaterialMaster(
            {
                ("MAT-100", "1000"): MaterialMasterRecord(
                    sap_material_number="MAT-100", plant="1000", available_quantity=500
                )
            }
        )
        graph = FakeGraph(error=RuntimeError("checkpoint missing"))
        service = self._build(graph=graph, material_master=material_master)
        self.po_lines.rows["po_line_1"] = {
            "id": "po_line_1",
            "batch_id": "batch_x",
            "plant": "1000",
            "po_number": "PO-1",
            "po_line_number": "10",
            "customer_id": "CUST-1",
            "customer_material_code": "MAT-REF",
            "order_quantity": 100,
            "uom": None,
            "status": "AWAITING_DECISION",
        }
        self.workflow_threads.rows["thread_po_1"] = {
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "stage": "AWAITING_MANUAL_CMIR_ENTRY",
            "status": "waiting_manual_cmir_entry",
            "current_node": "human_manual_cmir_entry",
            "pending_action_id": "00000000-0000-0000-0000-000000005001",
            "updated_at": "2026-08-09T10:00:00+00:00",
        }
        self.pending_actions.open_action = {
            "id": "00000000-0000-0000-0000-000000005001",
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "interrupt_type": "manual_cmir_entry",
            "payload": {},
            "state_snapshot": {},
            "created_at": "2026-08-09T10:00:00+00:00",
        }

        with self.assertRaises(ServiceError) as raised:
            service.submit_manual_cmir_entry(
                "thread_po_1",
                actor="csr@company.com",
                sap_material_number="MAT-100",
                expected_updated_at="2026-08-09T10:00:00+00:00",
            )

        self.assertEqual(raised.exception.code, "WORKFLOW_RESUME_FAILED")
        # The invariant: a failed resume must not close the open pending action.
        self.assertIsNotNone(self.pending_actions.open_action)

    def test_submit_qty_mismatch_decision_persists_decision_on_hitl_action(self) -> None:
        """The human's actual decision (proceed_anyway/mark_stale/use_substitute) must
        reach hitl_actions.decision via apply_human_action's decision= kwarg -- it was
        being silently dropped (resume_context["decision"] was built but never passed
        through _handle_graph_state's apply_human_action calls)."""
        service = self._build(graph=FakeGraph(result={}))
        self.po_lines.rows["po_line_1"] = {
            "id": "po_line_1",
            "batch_id": "batch_x",
            "plant": "1000",
            "po_number": "PO-1",
            "po_line_number": "10",
            "customer_id": "CUST-1",
            "customer_material_code": "MAT-REF",
            "order_quantity": 100,
            "uom": None,
            "status": "AWAITING_DECISION",
        }
        self.po_lines.forced_final_status = "READY_FOR_SO_CREATION_PARTIAL"
        self.workflow_threads.rows["thread_po_1"] = {
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "stage": "AWAITING_QTY_MISMATCH_DECISION",
            "status": "waiting_qty_mismatch_decision",
            "current_node": "human_qty_mismatch_decision",
            "pending_action_id": "00000000-0000-0000-0000-000000005001",
            "updated_at": "2026-08-09T10:00:00+00:00",
        }
        self.pending_actions.open_action = {
            "id": "00000000-0000-0000-0000-000000005001",
            "batch_id": "batch_x",
            "agent_run_id": "00000000-0000-0000-0000-000000009001",
            "thread_id": "thread_po_1",
            "email_id": None,
            "po_line_id": "po_line_1",
            "interrupt_type": "qty_mismatch_decision",
            "payload": {"candidate": {"suggested_substitute_material_code": None}},
            "state_snapshot": {},
            "created_at": "2026-08-09T10:00:00+00:00",
        }

        response = service.submit_qty_mismatch_decision(
            "thread_po_1",
            actor="csr@company.com",
            decision="proceed_anyway",
            expected_updated_at="2026-08-09T10:00:00+00:00",
        )

        self.assertEqual(response["stage"], "READY_FOR_SO_CREATION_PARTIAL")
        self.assertEqual(self.hitl_state.calls[-1]["decision"], "proceed_anyway")

    def test_get_errors_rejects_unknown_po_line(self) -> None:
        service = self._build(graph=FakeGraph())

        with self.assertRaises(ServiceError) as raised:
            service.get_errors("does-not-exist")

        self.assertEqual(raised.exception.code, "VALIDATION_ERROR")

    def _graph_of(self, service: PoValidationService) -> FakeGraph:
        return service._graph


if __name__ == "__main__":
    unittest.main()
