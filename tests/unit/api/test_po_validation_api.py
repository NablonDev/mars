from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.core.exceptions import ServiceError
from app.main import create_app


class FakeCMIRRunService:
    """Minimal CMIR fake so routes shared with the PO router don't touch the real Container."""

    def get_snapshot(self, thread_id):
        if thread_id == "thread_cmir_1":
            return {
                "batch_id": "batch_01",
                "agent_run_id": "00000000-0000-0000-0000-000000000001",
                "thread_id": thread_id,
                "email": {"email_id": "e1", "sender": "a@b.com", "subject": "CMIR"},
                "stage": "AWAITING_APPROVAL",
                "editable_fields": [],
                "cmir": {},
                "history": [],
                "updated_at": "2026-08-09T10:00:00+00:00",
            }
        raise ServiceError(
            "THREAD_NOT_FOUND", "Unknown thread_id.", status_code=404, details={"thread_id": thread_id}
        )


class FakePoValidationService:
    def __init__(self) -> None:
        self.ingested = []

    def ingest_po_lines(self, lines):
        self.ingested.append(lines)
        return {
            "batch_id": "batch_po_01",
            "total_lines": len(lines),
            "lines": [
                {
                    "po_line_id": "po_line_1",
                    "batch_id": "batch_po_01",
                    "po_number": line["po_number"],
                    "po_line_number": line["po_line_number"],
                    "status": "READY_FOR_SO_CREATION",
                    "thread_id": None,
                    "updated_at": None,
                }
                for line in lines
            ],
        }

    def list_ready_lines(self, **kwargs):
        return {"items": [{"id": "po_line_1", "status": "READY_FOR_SO_CREATION"}], "next_cursor": None}

    def get_errors(self, po_line_id):
        if po_line_id == "missing":
            raise ServiceError("VALIDATION_ERROR", "Unknown po_line_id.", status_code=422, details={})
        return {"items": []}

    def get_snapshot(self, thread_id):
        if thread_id == "thread_po_1":
            return {
                "agent_run_id": "00000000-0000-0000-0000-000000000042",
                "thread_id": thread_id,
                "po_line_id": "po_line_1",
                "po_number": "PO-1",
                "po_line_number": "10",
                "customer_material_code": "ACME-MAT-1",
                "order_quantity": 100,
                "stage": "AWAITING_QTY_MISMATCH_DECISION",
                "candidate": {
                    "sap_material_number": "MAT-1",
                    "plant": "1000",
                    "available_quantity": 40,
                    "shortfall": 60,
                    "suggested_substitute_material_code": "MAT-SUB",
                },
                "editable_fields": ["substitute_material_code"],
                "history": [],
                "updated_at": "2026-08-09T10:00:00+00:00",
            }
        raise ServiceError(
            "THREAD_NOT_FOUND", "Unknown thread_id.", status_code=404, details={"thread_id": thread_id}
        )

    def submit_qty_mismatch_decision(self, thread_id, **kwargs):
        return {
            "batch_id": "batch_po_01",
            "agent_run_id": "00000000-0000-0000-0000-000000000042",
            "thread_id": thread_id,
            "po_line_id": "po_line_1",
            "stage": "READY_FOR_SO_CREATION_PARTIAL",
            "status": "ready_for_so_creation_partial",
            "current_node": None,
            "pending_action_id": None,
            "updated_at": "2026-08-09T10:01:00+00:00",
        }

    def submit_manual_cmir_entry(self, thread_id, **kwargs):
        return {
            "batch_id": "batch_po_01",
            "agent_run_id": "00000000-0000-0000-0000-000000000042",
            "thread_id": thread_id,
            "po_line_id": "po_line_1",
            "stage": "READY_FOR_SO_CREATION",
            "status": "ready_for_so_creation",
            "current_node": None,
            "pending_action_id": None,
            "updated_at": "2026-08-09T10:01:00+00:00",
        }


class PoValidationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(FakeCMIRRunService(), FakePoValidationService()))

    def test_ingest_po_lines_returns_accepted_batch(self) -> None:
        response = self.client.post(
            "/api/v1/ingest/po-lines",
            json={
                "lines": [
                    {
                        "po_number": "PO-1",
                        "po_line_number": "10",
                        "customer_id": "CUST-1",
                        "customer_material_code": "ACME-MAT-1",
                        "plant": "1000",
                        "order_quantity": 100,
                    }
                ]
            },
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["batch_id"], "batch_po_01")
        self.assertEqual(body["lines"][0]["status"], "READY_FOR_SO_CREATION")
        self.assertIsNone(body["lines"][0]["thread_id"])

    def test_list_po_lines(self) -> None:
        response = self.client.get("/api/v1/po-lines?status=READY_FOR_SO_CREATION")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["id"], "po_line_1")

    def test_get_po_line_errors_unknown_line(self) -> None:
        response = self.client.get("/api/v1/po-lines/missing/errors")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_qty_mismatch_decision_route(self) -> None:
        response = self.client.post(
            "/api/v1/threads/thread_po_1/qty-mismatch-decision",
            json={
                "actor": "csr@company.com",
                "decision": "proceed_anyway",
                "expected_updated_at": "2026-08-09T10:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stage"], "READY_FOR_SO_CREATION_PARTIAL")

    def test_manual_cmir_entry_route(self) -> None:
        response = self.client.post(
            "/api/v1/threads/thread_po_1/manual-cmir-entry",
            json={
                "actor": "csr@company.com",
                "sap_material_number": "MAT-100",
                "description": "Legacy SKU",
                "expected_updated_at": "2026-08-09T10:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stage"], "READY_FOR_SO_CREATION")

    def test_snapshot_dispatches_to_po_service_for_po_thread(self) -> None:
        response = self.client.get("/api/v1/threads/thread_po_1/snapshot")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["po_line_id"], "po_line_1")
        self.assertEqual(body["candidate"]["suggested_substitute_material_code"], "MAT-SUB")

    def test_snapshot_falls_back_to_cmir_service_for_cmir_thread(self) -> None:
        response = self.client.get("/api/v1/threads/thread_cmir_1/snapshot")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["email"]["email_id"], "e1")

    def test_snapshot_unknown_thread_returns_404(self) -> None:
        response = self.client.get("/api/v1/threads/does-not-exist/snapshot")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "THREAD_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
