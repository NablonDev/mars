"""Shared UTC clock helpers -- avoids re-deriving `date`/naive-`datetime`
variants of `datetime.now(UTC)` at every call site."""

from __future__ import annotations

from datetime import UTC, date, datetime


def utc_now() -> datetime:
    """Current time, timezone-aware UTC."""
    return datetime.now(UTC)


def utc_today() -> date:
    """Current calendar date in UTC."""
    return utc_now().date()


def utc_now_naive() -> datetime:
    """Current time, UTC, with tzinfo stripped -- for columns/APIs that
    expect a naive datetime already understood to be UTC."""
    return utc_now().replace(tzinfo=None)
