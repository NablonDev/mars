"""Unit tests for the shared rate-limit gate (`app.core.rate_limit`).

This module was extracted from `app.workers.loop` (which previously held
its own private `_RateLimitGate`/`_looks_like_rate_limit`) so the
on-demand API path (`app.api.dependencies.get_fine_projection_summary_job_runner`)
could share the exact same mechanism. Both call sites now import
`RateLimitGate`/`looks_like_rate_limit` directly from here -- no
underscore-prefixed aliases remain anywhere (see
`tests/unit/workers/test_worker_loop.py`, which imports the same public
names). This file exercises the behaviour directly, so the canonical
module has its own coverage independent of either call site.
"""

from __future__ import annotations

import threading
import time

from app.core.exceptions import ExternalServiceError, NotFoundError
from app.core.rate_limit import RateLimitGate, looks_like_rate_limit


def test_looks_like_rate_limit_matches_status_code_and_phrases():
    assert looks_like_rate_limit(RuntimeError("429 Too Many Requests")) is True
    assert looks_like_rate_limit(RuntimeError("Rate limit exceeded, back off")) is True
    assert looks_like_rate_limit(RuntimeError("quota exceeded for this deployment")) is True


def test_looks_like_rate_limit_walks_the_cause_chain():
    try:
        try:
            raise RuntimeError("429 Too Many Requests")
        except RuntimeError as inner:
            raise ExternalServiceError(
                code="PENALTY_PROJECTION_SUMMARY_UPSTREAM_FAILED", message="upstream failed"
            ) from inner
    except ExternalServiceError as outer:
        assert looks_like_rate_limit(outer) is True


def test_looks_like_rate_limit_false_for_unrelated_errors():
    assert looks_like_rate_limit(RuntimeError("connection reset by peer")) is False
    assert looks_like_rate_limit(NotFoundError(code="PO_NOT_FOUND", message="ORD-1")) is False


def test_rate_limit_gate_pauses_until_backoff_elapses():
    gate = RateLimitGate()
    shutdown = threading.Event()

    gate.note_rate_limit_hit(0.15)
    start = time.monotonic()
    gate.wait_if_paused(shutdown)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.1
    assert gate.rate_limit_hits == 1


def test_rate_limit_gate_second_hit_extends_not_shortens_the_pause():
    gate = RateLimitGate()
    gate.note_rate_limit_hit(0.05)
    gate.note_rate_limit_hit(0.3)  # a later, longer pause must win
    shutdown = threading.Event()

    start = time.monotonic()
    gate.wait_if_paused(shutdown)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.25
    assert gate.rate_limit_hits == 2


def test_rate_limit_gate_returns_immediately_once_shutdown_is_set():
    gate = RateLimitGate()
    gate.note_rate_limit_hit(30)
    shutdown = threading.Event()
    shutdown.set()

    start = time.monotonic()
    gate.wait_if_paused(shutdown)
    assert time.monotonic() - start < 1.0


def test_rate_limit_gate_no_pause_when_never_hit():
    gate = RateLimitGate()
    shutdown = threading.Event()

    start = time.monotonic()
    gate.wait_if_paused(shutdown)

    assert time.monotonic() - start < 0.1
    assert gate.rate_limit_hits == 0
