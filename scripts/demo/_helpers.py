"""Shared polling/error-formatting helpers for the httpx-based demo scripts.

Extracted from `demo_fine_summary.py` and `run_end_to_end_demo.py`, which
both defined `_error_message`/`_poll_until_ready` and the
`POLL_INTERVAL_SECONDS`/`POLL_TIMEOUT_SECONDS` constants verbatim -- kept
here once so the two scripts stop carrying duplicate copies.
"""

import time

import httpx

POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 120.0


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
    """Polls `GET .../summary` until the job leaves PENDING, or gives
    up after POLL_TIMEOUT_SECONDS. Returns the job body (status READY or
    FAILED) or None on timeout."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = httpx.get(
            f"{base_url}/orders/{order_id}/summary", params={"as_of_date": as_of_date}, timeout=30
        )
        resp.raise_for_status()
        job = resp.json()
        if job["status"] != "PENDING":
            return job
        time.sleep(POLL_INTERVAL_SECONDS)
    return None
