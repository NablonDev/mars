"""Shared UTC clock helpers, so no call site re-derives its own `datetime.now(UTC)`."""

from __future__ import annotations

from datetime import UTC, date, datetime


def utc_now() -> datetime:
    """Current time, timezone-aware UTC."""
    return datetime.now(UTC)


def utc_today() -> date:
    """Current calendar date in UTC."""
    return utc_now().date()


def utc_now_naive() -> datetime:
    """Current UTC time with tzinfo stripped, for columns that expect a naive value."""
    return utc_now().replace(tzinfo=None)
