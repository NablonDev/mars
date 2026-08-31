from __future__ import annotations

import unittest

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.po_validation.graph import build_po_validation_graph
from app.agents.po_validation.nodes import PoValidationNodes

INTERRUPT_KEY = "__interrupt__"


class FakeTraceRepo:
    def log(self, *args, **kwargs):
        pass


class FakePurchaseOrderRepository:
    def __init__(self) -> None:
        self.statuses: dict = {}

    def update_line_status(self, purchase_order_line_id, line_status):
        self.statuses[purchase_order_line_id] = line_status


class FakeMasterDataRepository:
    """Mirrors app.repositories.common.master_data.MasterDataRepository's dict
    shape (find_material_master returns a plain dict, not an ORM row)."""

    def __init__(self, records: dict) -> None:
        self.records = records

    def find_material_master(self, sap_material_number, plant_id):
        return self.records.get((sap_material_number, plant_id))


class FakeCmirRepository:
    def __init__(self, match=None) -> None:
        self.match = match
        self.created = []

    def find_latest_for_customer_material(self, customer_identity, target_customer_material_ref):
        return self.match

    def create_manual_mapping(self, **kwargs):
        self.created.append(kwargs)
        return len(self.created)


class FakeProcessingErrorRepository:
    def __init__(self) -> None:
        self.logged = []

    def log(self, error_type, **kwargs):
        entry = {"error_type": error_type, **kwargs}
        self.logged.append(entry)
        return entry


def _po_line(**overrides):
    line = {
        "po_number": "PO-1",
        "po_line_number": "10",
        "customer_id": "CUST-1",
        "customer_material_code": "ACME-MAT-1",
        "plant": "1000",
        "plant_id": "1000",
        "order_quantity": 100,
        "uom": "EA",
    }
    line.update(overrides)
    return line


def _material_master(sap_material_number, plant_id, available_quantity, follow_up_material_id=None):
    return {
        "sap_material_number": sap_material_number,
        "plant_id": plant_id,
        "available_quantity": available_quantity,
        "follow_up_material_id": follow_up_material_id,
    }


class PoValidationWorkflowTests(unittest.TestCase):
    def _build_graph(self, *, cmir_match=None, material_records=None, processing_errors=None):
        self.purchase_orders = FakePurchaseOrderRepository()
        self.master_data = FakeMasterDataRepository(material_records or {})
        self.cmir_repository = FakeCmirRepository(match=cmir_match)
        self.processing_errors = processing_errors or FakeProcessingErrorRepository()
        nodes = PoValidationNodes(
            purchase_order_repository=self.purchase_orders,
            master_data_repository=self.master_data,
            cmir_repository=self.cmir_repository,
            processing_error_repository=self.processing_errors,
        )
        return build_po_validation_graph(nodes, MemorySaver(), FakeTraceRepo())

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def test_touchless_path_when_quantity_sufficient(self) -> None:
        graph = self._build_graph(
            cmir_match={"material_identity": "MAT-1"},
            material_records={("MAT-1", "1000"): _material_master("MAT-1", "1000", 500)},
        )
        state = graph.invoke(
            {
                "po_line_id": "line-1",
                "run_id": "00000000-0000-0000-0000-000000000001",
                "batch_id": "batch-1",
                "po_line": _po_line(),
            },
            config=self._config("t1"),
        )

        self.assertNotIn(INTERRUPT_KEY, state)
        self.assertEqual(self.purchase_orders.statuses["line-1"], "READY_FOR_SO_CREATION")

    def test_qty_mismatch_offers_one_hop_substitute_and_proceed_anyway(self) -> None:
        graph = self._build_graph(
            cmir_match={"material_identity": "MAT-1"},
            material_records={
                ("MAT-1", "1000"): _material_master("MAT-1", "1000", 40, follow_up_material_id="MAT-SUB")
            },
        )
        state = graph.invoke(
            {
                "po_line_id": "line-2",
                "run_id": "00000000-0000-0000-0000-000000000001",
                "batch_id": "batch-1",
                "po_line": _po_line(),
            },
            config=self._config("t2"),
        )

        self.assertIn(INTERRUPT_KEY, state)
        payload = state[INTERRUPT_KEY][0].value
        self.assertEqual(payload["reason"], "qty_mismatch_decision")
        self.assertEqual(payload["candidate"]["suggested_substitute_material_code"], "MAT-SUB")
        self.assertEqual(payload["candidate"]["shortfall"], 60)

        state = graph.invoke(Command(resume={"decision": "proceed_anyway"}), config=self._config("t2"))

        self.assertNotIn(INTERRUPT_KEY, state)
        self.assertEqual(self.purchase_orders.statuses["line-2"], "READY_FOR_SO_CREATION_PARTIAL")

    def test_qty_mismatch_mark_stale(self) -> None:
        graph = self._build_graph(
            cmir_match={"material_identity": "MAT-1"},
            material_records={("MAT-1", "1000"): _material_master("MAT-1", "1000", 1)},
        )
        graph.invoke(
            {
                "po_line_id": "line-3",
                "run_id": "00000000-0000-0000-0000-000000000001",
                "batch_id": "batch-1",
                "po_line": _po_line(),
            },
            config=self._config("t3"),
        )
        state = graph.invoke(Command(resume={"decision": "mark_stale"}), config=self._config("t3"))

        self.assertNotIn(INTERRUPT_KEY, state)
        self.assertEqual(self.purchase_orders.statuses["line-3"], "DISCONTINUED")

    def test_use_substitute_that_is_still_short_re_triggers_mismatch(self) -> None:
        graph = self._build_graph(
            cmir_match={"material_identity": "MAT-1"},
            material_records={
                ("MAT-1", "1000"): _material_master("MAT-1", "1000", 10, follow_up_material_id="MAT-SUB"),
                ("MAT-SUB", "1000"): _material_master("MAT-SUB", "1000", 5),
            },
        )
        graph.invoke(
            {
                "po_line_id": "line-4",
                "run_id": "00000000-0000-0000-0000-000000000001",
                "batch_id": "batch-1",
                "po_line": _po_line(),
            },
            config=self._config("t4"),
        )
        state = graph.invoke(Command(resume={"decision": "use_substitute"}), config=self._config("t4"))

        self.assertIn(INTERRUPT_KEY, state)
        payload = state[INTERRUPT_KEY][0].value
        self.assertEqual(payload["reason"], "qty_mismatch_decision")
        self.assertEqual(payload["candidate"]["sap_material_number"], "MAT-SUB")

    def test_use_substitute_that_is_now_sufficient_completes(self) -> None:
        graph = self._build_graph(
            cmir_match={"material_identity": "MAT-1"},
            material_records={
                ("MAT-1", "1000"): _material_master("MAT-1", "1000", 10, follow_up_material_id="MAT-SUB"),
                ("MAT-SUB", "1000"): _material_master("MAT-SUB", "1000", 999),
            },
        )
        graph.invoke(
            {
                "po_line_id": "line-5",
                "run_id": "00000000-0000-0000-0000-000000000001",
                "batch_id": "batch-1",
                "po_line": _po_line(),
            },
            config=self._config("t5"),
        )
        state = graph.invoke(Command(resume={"decision": "use_substitute"}), config=self._config("t5"))

        self.assertNotIn(INTERRUPT_KEY, state)
        self.assertEqual(self.purchase_orders.statuses["line-5"], "READY_FOR_SO_CREATION")

    def test_no_cmir_match_routes_to_manual_entry_then_loops_back(self) -> None:
        graph = self._build_graph(
            cmir_match=None,
            material_records={("MAT-100", "1000"): _material_master("MAT-100", "1000", 500)},
        )
        state = graph.invoke(
            {
                "po_line_id": "line-6",
                "run_id": "00000000-0000-0000-0000-000000000001",
                "batch_id": "batch-1",
                "po_line": _po_line(),
            },
            config=self._config("t6"),
        )

        self.assertIn(INTERRUPT_KEY, state)
        payload = state[INTERRUPT_KEY][0].value
        self.assertEqual(payload["reason"], "manual_cmir_entry")

        state = graph.invoke(
            Command(resume={"sap_material_number": "MAT-100", "description": "Legacy SKU"}),
            config=self._config("t6"),
        )

        self.assertNotIn(INTERRUPT_KEY, state)
        self.assertEqual(self.purchase_orders.statuses["line-6"], "READY_FOR_SO_CREATION")
        self.assertEqual(len(self.cmir_repository.created), 1)
        self.assertEqual(self.cmir_repository.created[0]["material_identity"], "MAT-100")

    def test_lookup_failure_routes_to_handle_error_and_logs_one_processing_error(self) -> None:
        graph = self._build_graph(
            cmir_match={"material_identity": "MAT-MISSING"},
            material_records={},  # no material_master row -> check_material_master raises
        )
        state = graph.invoke(
            {
                "po_line_id": "line-7",
                "run_id": "00000000-0000-0000-0000-000000000001",
                "batch_id": "batch-1",
                "po_line": _po_line(),
            },
            config=self._config("t7"),
        )

        self.assertNotIn(INTERRUPT_KEY, state)
        self.assertEqual(self.purchase_orders.statuses["line-7"], "FAILED")
        self.assertEqual(len(self.processing_errors.logged), 1)
        self.assertEqual(self.processing_errors.logged[0]["error_type"], "LOOKUP_FAILURE")
        self.assertEqual(self.processing_errors.logged[0]["node_name"], "check_material_master")


if __name__ == "__main__":
    unittest.main()
