"""Merges a proposed CMIR draft onto the currently active record.

Moved unchanged from `app/services/cmir_merge.py` (Phase 3 -- services
move/folder-split). No repository/model dependency here, so nothing else
changed.
"""

from __future__ import annotations

from typing import Any

from app.schemas.cmir import CMIR_CONTENT_FIELDS, Cmir


def merge_with_active(
    existing: dict[str, Any] | None, proposed: Cmir
) -> tuple[Cmir, dict[str, dict[str, Any]]]:
    """Merge a proposed CMIR draft onto the currently active record, if one exists.

    For every field in CMIR_CONTENT_FIELDS, the proposed value wins when it is
    non-blank; a blank proposed value carries the existing record's value forward
    instead of overwriting it with nothing. This is what lets an email that only
    mentions one changed field (e.g. a new effective_date) update just that field
    without blanking out everything else already on file.

    `existing` is expected to carry all of CMIR_CONTENT_FIELDS (the shape
    `CmirRecordRepository.get_current(...)` returns) or be None for a genuine
    create -- a missing key is treated the same as a blank value.

    Deterministic, framework-free, and safe to call from both a LangGraph node
    (prepare_diff) and a plain service method (update_draft) against a freshly
    fetched active record -- the same reasons CmirValidator is kept separate from
    the LLM extraction step apply here: this decision should never depend on model
    behavior.

    Returns the merged CMIR plus a field-level diff (existing -> merged), shaped
    like the field_changes dicts already used for `human_action` audit rows
    elsewhere in this codebase (see `app/services/cmir/run_service.py`::update_draft).
    Only fields that actually changed are included -- a field the proposed draft
    left blank, which therefore carried forward unchanged, never appears in the
    diff. For a genuine create (existing=None), every non-blank proposed field
    appears as a change from "".
    """
    existing = existing or {}
    merged_values: dict[str, str] = {}
    diff: dict[str, dict[str, Any]] = {}

    for field_name in CMIR_CONTENT_FIELDS:
        proposed_value = str(getattr(proposed, field_name, "") or "")
        existing_value = str(existing.get(field_name) or "")
        merged_value = proposed_value if proposed_value.strip() else existing_value

        merged_values[field_name] = merged_value
        if merged_value != existing_value:
            diff[field_name] = {"from": existing_value, "to": merged_value}

    merged = Cmir.model_validate(merged_values)
    return merged, diff
