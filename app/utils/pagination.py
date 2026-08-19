"""Shared helpers for cursor-based pagination (CMIR/PO Validation list endpoints)."""

from __future__ import annotations

from datetime import datetime


def parse_cursor(cursor: str | None) -> datetime | None:
    """Parse an opaque ``updated_at`` pagination cursor back into a datetime.

    Every ``list_*`` repository method here mints its ``next_cursor`` by
    serializing a row's ``updated_at`` to ISO 8601 (see ``_iso()`` in
    observability.py/po_validation.py) and hands it back to the client
    verbatim; clients round-trip that same string back in as ``cursor`` on
    the next page request. Filtering a ``timestamptz`` column against the
    raw ``str`` binds it ``::VARCHAR``, which Postgres rejects
    (``UndefinedFunction``) -- this restores the datetime type before the
    value reaches a query.

    Raises ``ValueError`` if ``cursor`` isn't a valid ISO 8601 timestamp;
    callers are expected to translate that into a 422 at the service layer.
    """
    if cursor is None:
        return None
    return datetime.fromisoformat(cursor)
