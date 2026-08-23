"""Shared polling/error-formatting helpers for the httpx-based demo scripts.

Extracted from `demo_fine_projection_summary.py` and `run_end_to_end_demo.py`, which
both defined `_error_message`/`_poll_until_ready` and the
`POLL_INTERVAL_SECONDS`/`POLL_TIMEOUT_SECONDS` constants verbatim -- kept
here once so the two scripts stop carrying duplicate copies.
"""

import os
import sys
import time

import httpx

POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 120.0


def _auth_headers() -> dict[str, str]:
    """Header for calling this app's own API from outside the app process.

    These scripts run against an already-running server, so this reads the
    raw env var rather than app.core.config.Settings (which they
    deliberately don't import -- they only need httpx).
    """
    key = os.environ.get("INTERNAL_API_KEY")
    if not key:
        print(
            "INTERNAL_API_KEY is not set in this shell -- export the same value "
            "the running server was started with.",
            file=sys.stderr,
        )
        sys.exit(1)
    return {"X-Internal-Api-Key": key}


def _error_message(resp: httpx.Response) -> str:
    """Handles both this app's `{"error": {"message": ...}}` envelope
    (app/core/exceptions.py) and the plain `{"detail": ...}` shape a few
    not-yet-migrated routes still raise via bare `HTTPException` --
    see the reviewer's carried-over note in PROGRESS.local.md."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text
    if isinstance(body, dict) and "error" in body:
        return body["error"].get("message", resp.text)
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return resp.text


def _poll_until_ready(base_url: str, order_id: str, as_of_date: str) -> dict | None:
    """Polls `GET .../projection-summary` until the job leaves PENDING, or gives
    up after POLL_TIMEOUT_SECONDS. Returns the job body (status READY or
    FAILED) or None on timeout."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    headers = _auth_headers()
    while time.monotonic() < deadline:
        resp = httpx.get(
            f"{base_url}/orders/{order_id}/projection-summary",
            params={"as_of_date": as_of_date},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        job = resp.json()
        if job["status"] != "PENDING":
            return job
        time.sleep(POLL_INTERVAL_SECONDS)
    return None
