"""Shared auth/error-formatting helpers for the httpx-based demo scripts.

Was shared by `demo_fine_projection_summary.py` and `run_end_to_end_demo.py`,
which both defined `_error_message`/`_poll_until_ready` verbatim -- kept here
once so the two scripts stop carrying duplicate copies.

The shared `_poll_until_ready` helper is gone: the pre-restructure API had
one shape for both fine-projection-summary and fine-mitigation-summary
polling (`GET /orders/{id}/{summary_path}?as_of_date=...`), so one function
covered both. The new API has no equivalent per-(purchase_order,date)
summary-poll endpoint for either domain -- `?include=summary` is a pure
read, nested under a different resource shape per domain
(`GET /purchase-orders/{id}/penalty-projections?include=summary` for
projection, `GET /penalty-mitigations?projection_id={id}&include=summary`
for mitigation, keyed by a projection's own surrogate id, not a date) -- so
each demo script now owns its own poll loop against its own shape. See
`demo_penalty_projection_summary.py`/`demo_penalty_mitigation_summary.py`.
"""

import os
import sys

import httpx

POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 120.0


def _auth_headers() -> dict[str, str]:
    """Header for calling this app's own API from outside the app process.

    These scripts run against an already-running server, so this reads the
    raw env var rather than app.core.config.Settings (which they
    deliberately don't import -- they only need httpx).
    """
    key = os.environ.get("APP_INTERNAL_API_KEY")
    if not key:
        print(
            "APP_INTERNAL_API_KEY is not set in this shell -- export the same value "
            "the running server was started with.",
            file=sys.stderr,
        )
        sys.exit(1)
    return {"X-Internal-Api-Key": key}


def _error_message(resp: httpx.Response) -> str:
    """Reads this app's `{success, message, data, error}` envelope
    (app/core/envelope.py) -- every error response (`AppError`, FastAPI's
    own `RequestValidationError`/`HTTPException`, and the unhandled-
    exception catch-all) is shaped through it (app/core/exceptions.py)."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text
    if isinstance(body, dict) and body.get("error"):
        return body["error"].get("details") or body.get("message", resp.text)
    if isinstance(body, dict) and "message" in body:
        return str(body["message"])
    return resp.text
