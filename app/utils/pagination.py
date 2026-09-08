"""Shared helpers for cursor-based pagination (CMIR/PO Validation list endpoints)."""

from __future__ import annotations

from datetime import datetime


def parse_cursor(cursor: str | None) -> datetime | None:
    """Parse an ISO 8601 `updated_at` cursor into a datetime; raises ValueError if malformed."""
    if cursor is None:
        return None
    return datetime.fromisoformat(cursor)
