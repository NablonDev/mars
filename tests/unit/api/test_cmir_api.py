from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.core.exceptions import ServiceError
from app.main import create_app


class FakeRunService:
    def start_email_ingest(self, **kwargs):
        return {
            "batch_id": "batch_01",
            "status": "running",
            "total_threads": 1,
            "threads": [
                {
                    "batch_id": "batch_01",
                    "agent_run_id": 1042,
                    "thread_id": "thread_01J4A",
                    "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
                    "stage": "AWAITING_APPROVAL",
                    "status": "waiting_approval",
                    "current_node": "review_extracted_cmir",
                    "pending_action_id": 3001,
                    "updated_at": "2026-07-30T10:30:00+00:00",
                }
            ],
        }

    def list_runs(self, **kwargs):
        return {"items": [], "next_cursor": None}

    def get_stage(self, thread_id):
        if thread_id == "missing":
            raise ServiceError(
                "THREAD_NOT_FOUND",
                "Unknown thread_id.",
                status_code=404,
                details={"thread_id": thread_id},
            )
        return {
            "batch_id": "batch_01",
            "agent_run_id": 1042,
            "thread_id": thread_id,
            "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
            "stage": "AWAITING_APPROVAL",
            "status": "waiting_approval",
            "current_node": "review_extracted_cmir",
            "pending_action_id": 3001,
            "updated_at": "2026-07-30T10:30:00+00:00",
        }

    def get_snapshot(self, thread_id):
        return {
            "batch_id": "batch_01",
            "agent_run_id": 1042,
            "thread_id": thread_id,
            "email": {
                "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
                "sender": "customer@example.com",
                "subject": "CMIR Request 1",
                "source_message_id": "msg-001",
            },
            "stage": "AWAITING_APPROVAL",
            "editable_fields": ["brand"],
            "cmir": {"brand": "Brand A"},
            "history": [],
            "updated_at": "2026-07-30T10:30:00+00:00",
        }

    def submit_missing_fields(self, thread_id, **kwargs):
        return self.get_stage(thread_id)

    def update_draft(self, thread_id, **kwargs):
        return {
            "batch_id": "batch_01",
            "agent_run_id": 1042,
            "thread_id": thread_id,
            "stage": "AWAITING_APPROVAL",
            "status": "waiting_approval",
            "pending_action_id": 3003,
            "message": "Draft saved. Review again.",
        }

    def submit_decision(self, thread_id, **kwargs):
        return self.get_stage(thread_id)


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(FakeRunService()))

    def test_ingest_emails_returns_accepted_batch_payload(self) -> None:
        response = self.client.post("/api/v1/ingest/emails", json={})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["batch_id"], "batch_01")
        self.assertEqual(response.json()["threads"][0]["thread_id"], "thread_01J4A")

    def test_get_thread_stage_uses_prd_error_contract(self) -> None:
        response = self.client.get("/api/v1/threads/missing/stage")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "THREAD_NOT_FOUND")

    def test_update_draft_route_returns_review_again_message(self) -> None:
        response = self.client.post(
            "/api/v1/threads/thread_01J4A/update",
            json={
                "actor": "reviewer@company.com",
                "fields": {"brand": "Brand A"},
                "expected_updated_at": "2026-07-30T10:30:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pending_action_id"], 3003)
        self.assertEqual(response.json()["message"], "Draft saved. Review again.")


if __name__ == "__main__":
    unittest.main()
