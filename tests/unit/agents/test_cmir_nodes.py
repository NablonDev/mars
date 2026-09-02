from __future__ import annotations

import unittest

from app.agents.cmir.nodes import WorkflowNodes


class FakeCmirRepository:
    def __init__(self, current=None) -> None:
        self.current = current
        self.lookups = []

    def get_current(self, customer_identity, target_customer_material_ref):
        self.lookups.append((customer_identity, target_customer_material_ref))
        return self.current


class FakeActionLogRepository:
    def __init__(self) -> None:
        self.logged = []

    def log(self, email_id, action, actor, details):
        self.logged.append((email_id, action, actor, details))


def _nodes(cmir_repository=None):
    return WorkflowNodes(
        email_reader=None,
        extractor=None,
        validator=None,
        email_repository=None,
        cmir_repository=cmir_repository or FakeCmirRepository(),
        action_log_repository=FakeActionLogRepository(),
    )


class IdentifyExistingCmirTests(unittest.TestCase):
    def test_looks_up_current_record_by_customer_and_material_ref(self) -> None:
        cmir_repository = FakeCmirRepository(current=None)
        nodes = _nodes(cmir_repository)

        result = nodes.identify_existing_cmir(
            {
                "cmir": {
                    "customer_identity": "Acme Manufacturing Ltd",
                    "target_customer_material_ref": "ACME-PE200-STD",
                }
            }
        )

        self.assertEqual(cmir_repository.lookups, [("Acme Manufacturing Ltd", "ACME-PE200-STD")])
        self.assertIsNone(result["existing_cmir"])

    def test_returns_found_record_under_existing_cmir_key(self) -> None:
        existing = {"id": 42, "customer_identity": "Acme Manufacturing Ltd", "brand": "AcmePlast"}
        nodes = _nodes(FakeCmirRepository(current=existing))

        result = nodes.identify_existing_cmir(
            {
                "cmir": {
                    "customer_identity": "Acme Manufacturing Ltd",
                    "target_customer_material_ref": "ACME-PE200-STD",
                }
            }
        )

        self.assertEqual(result["existing_cmir"], existing)


class PrepareDiffTests(unittest.TestCase):
    def test_create_path_has_no_version_token_and_full_diff(self) -> None:
        nodes = _nodes()

        result = nodes.prepare_diff(
            {
                "existing_cmir": None,
                "cmir": {"customer_identity": "Acme Manufacturing Ltd", "brand": "AcmePlast"},
            }
        )

        self.assertIsNone(result["cmir_version_token"])
        self.assertEqual(result["cmir_diff"]["brand"], {"from": "", "to": "AcmePlast"})
        self.assertEqual(result["cmir"]["brand"], "AcmePlast")

    def test_update_path_carries_version_token_and_merges_blank_fields_forward(self) -> None:
        nodes = _nodes()
        existing = {
            "id": 42,
            "customer_identity": "Acme Manufacturing Ltd",
            "material_identity": "Polyethylene Resin PE-200",
            "sender_type": "external",
            "intent_phrase": "update",
            "existing_cmir_ref": "CMIR-2025-0099",
            "brand": "AcmePlast",
            "site": "Site 12",
            "target_grd_code": "GRD-01",
            "target_customer_material_ref": "ACME-PE200-STD",
            "effective_date": "2026-01-01",
            "reason": "Annual refresh",
        }

        result = nodes.prepare_diff(
            {
                "existing_cmir": existing,
                # This email only mentions a new effective_date -- everything else
                # came back blank from extraction.
                "cmir": {"effective_date": "2026-09-01"},
            }
        )

        self.assertEqual(result["cmir_version_token"], 42)
        self.assertEqual(result["cmir_diff"], {"effective_date": {"from": "2026-01-01", "to": "2026-09-01"}})
        self.assertEqual(result["cmir"]["brand"], "AcmePlast")
        self.assertEqual(result["cmir"]["effective_date"], "2026-09-01")


class HandleVersionConflictTests(unittest.TestCase):
    def test_logs_conflict_with_entity_identity(self) -> None:
        action_log = FakeActionLogRepository()
        nodes = WorkflowNodes(
            email_reader=None,
            extractor=None,
            validator=None,
            email_repository=None,
            cmir_repository=FakeCmirRepository(),
            action_log_repository=action_log,
        )

        result = nodes.handle_version_conflict(
            {
                "email_id": "email-1",
                "cmir": {
                    "customer_identity": "Acme Manufacturing Ltd",
                    "target_customer_material_ref": "ACME-PE200-STD",
                },
            }
        )

        self.assertEqual(result, {})
        self.assertEqual(len(action_log.logged), 1)
        email_id, action, _actor, details = action_log.logged[0]
        self.assertEqual(email_id, "email-1")
        self.assertEqual(action, "CMIR Version Conflict")
        self.assertEqual(details["customer_identity"], "Acme Manufacturing Ltd")
        self.assertEqual(details["target_customer_material_ref"], "ACME-PE200-STD")


if __name__ == "__main__":
    unittest.main()
