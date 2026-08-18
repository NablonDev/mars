from __future__ import annotations

import unittest

from app.schemas.cmir import CMIR, CMIR_CONTENT_FIELDS
from app.services.cmir_merge import merge_with_active


class MergeWithActiveTests(unittest.TestCase):
    def test_create_with_no_existing_record_diffs_every_non_blank_field_from_blank(self) -> None:
        proposed = CMIR(
            sender_type="external",
            customer_identity="Acme Manufacturing Ltd",
            material_identity="Polyethylene Resin PE-200",
            intent_phrase="update",
            existing_cmir_ref="",
            brand="AcmePlast",
            site="Site 12",
            target_customer_material_ref="ACME-PE200-STD",
        )

        merged, diff = merge_with_active(None, proposed)

        self.assertEqual(merged.customer_identity, "Acme Manufacturing Ltd")
        self.assertEqual(merged.material_identity, "Polyethylene Resin PE-200")
        # Fields the proposed draft left blank stay blank when there's nothing to
        # carry forward from -- there is no existing record at all.
        self.assertEqual(merged.existing_cmir_ref, "")
        self.assertEqual(merged.effective_date, "")

        self.assertEqual(diff["customer_identity"], {"from": "", "to": "Acme Manufacturing Ltd"})
        self.assertEqual(diff["brand"], {"from": "", "to": "AcmePlast"})
        # A field that stayed blank both before (nothing) and after is not a change.
        self.assertNotIn("existing_cmir_ref", diff)
        self.assertNotIn("effective_date", diff)

    def test_blank_proposed_field_carries_forward_existing_value_with_no_diff_entry(self) -> None:
        existing = {
            "sender_type": "external",
            "customer_identity": "Acme Manufacturing Ltd",
            "material_identity": "Polyethylene Resin PE-200",
            "intent_phrase": "update",
            "existing_cmir_ref": "CMIR-2025-0099",
            "brand": "AcmePlast",
            "site": "Site 12",
            "target_grd_code": "GRD-01",
            "target_customer_material_ref": "ACME-PE200-STD",
            "effective_date": "2026-01-01",
            "reason": "Annual refresh",
        }
        # Only the effective_date changed; everything else was left blank by the LLM
        # extraction for this email (a common real case -- most fields don't change).
        proposed = CMIR(effective_date="2026-09-01")

        merged, diff = merge_with_active(existing, proposed)

        self.assertEqual(merged.customer_identity, "Acme Manufacturing Ltd")
        self.assertEqual(merged.brand, "AcmePlast")
        self.assertEqual(merged.reason, "Annual refresh")
        self.assertEqual(merged.effective_date, "2026-09-01")

        self.assertEqual(diff, {"effective_date": {"from": "2026-01-01", "to": "2026-09-01"}})

    def test_non_blank_proposed_value_overwrites_existing_value(self) -> None:
        existing = {"customer_identity": "Acme Manufacturing Ltd", "brand": "AcmePlast"}
        proposed = CMIR(customer_identity="Acme Manufacturing Ltd", brand="AcmePlast Europe")

        merged, diff = merge_with_active(existing, proposed)

        self.assertEqual(merged.brand, "AcmePlast Europe")
        self.assertEqual(diff, {"brand": {"from": "AcmePlast", "to": "AcmePlast Europe"}})

    def test_whitespace_only_proposed_value_is_treated_as_blank(self) -> None:
        existing = {"reason": "Original reason"}
        proposed = CMIR(reason="   ")

        merged, diff = merge_with_active(existing, proposed)

        self.assertEqual(merged.reason, "Original reason")
        self.assertNotIn("reason", diff)

    def test_status_and_missing_fields_are_never_part_of_merge_or_diff(self) -> None:
        existing = {"customer_identity": "Acme Manufacturing Ltd"}
        proposed = CMIR(
            customer_identity="Acme Manufacturing Ltd",
            status="pending_human_action",
            missing_fields=["brand", "site"],
        )

        merged, diff = merge_with_active(existing, proposed)

        # merge_with_active resets these -- CMIRValidator recomputes them afterwards
        # from the merged content, exactly like it does today for a fresh draft.
        self.assertEqual(merged.status, "")
        self.assertEqual(merged.missing_fields, [])
        self.assertNotIn("status", diff)
        self.assertNotIn("missing_fields", diff)

    def test_existing_missing_a_key_entirely_is_treated_as_blank(self) -> None:
        # A partial dict (e.g. a hand-built fixture, or a future schema change that
        # adds a field get_current doesn't populate yet) must not raise.
        existing = {"customer_identity": "Acme Manufacturing Ltd"}
        proposed = CMIR(customer_identity="Acme Manufacturing Ltd", brand="AcmePlast")

        merged, diff = merge_with_active(existing, proposed)

        self.assertEqual(merged.brand, "AcmePlast")
        self.assertEqual(diff["brand"], {"from": "", "to": "AcmePlast"})

    def test_merge_covers_every_declared_content_field(self) -> None:
        existing = {name: f"existing-{name}" for name in CMIR_CONTENT_FIELDS}
        proposed = CMIR()  # every field left blank by this "email"

        merged, diff = merge_with_active(existing, proposed)

        for field_name in CMIR_CONTENT_FIELDS:
            self.assertEqual(getattr(merged, field_name), f"existing-{field_name}")
        self.assertEqual(diff, {})


if __name__ == "__main__":
    unittest.main()
