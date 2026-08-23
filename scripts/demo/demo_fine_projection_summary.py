"""
Calls the LLM-powered fine-projection-summary endpoint
(`POST /orders/{order_id}/projection-summary`) for one or every order and prints
the free-text result -- the "why is this order's number what it is"
companion to `demo_daily_simulation.py`'s "what is the number."

Generation is a background job: a cache miss (or --force-regenerate) gets
a `202` immediately, not a `200` with the summary already in it -- this
script polls `GET .../projection-summary` until the job leaves `PENDING`, per
`docs/API.md` "Fine Projection Summaries."

Requires real Azure OpenAI credentials in `.env`
(AZURE_OPENAI_API_KEY/ENDPOINT/DEPLOYMENT_NAME) -- without them the
background job lands on a FAILED status with a clear "Fine projection
summary generation failed upstream" message (see
app/services/fine_projection/summary.py), which this script prints
per-order and moves on rather than treating as a script bug.

For each order this explicitly looks up its latest existing projection
date via `GET /orders/{id}/projections` and passes that as `as_of_date`
-- it never omits `as_of_date` and relies on the server's
"defaults to today" behaviour, since that only happens to produce a
sensible answer while the mock scenario dates (Aug 2026) and the real
calendar date coincide. Summarizing an order with no projections yet is a
clean skip, not a crash -- run `demo_daily_simulation.py` (or
`POST /orders/{order_id}/projections`) first.

Usage:
    uvicorn app.main:app --reload &
    python scripts/demo/seed_master_data.py
    python scripts/demo/demo_daily_simulation.py

    python scripts/demo/demo_fine_projection_summary.py                              # every order on file
    python scripts/demo/demo_fine_projection_summary.py --order-id WMT-100234         # one order
    python scripts/demo/demo_fine_projection_summary.py --order-id WMT-100234 --force-regenerate
"""

import argparse
import sys
from datetime import UTC, datetime

import httpx
from _helpers import POLL_TIMEOUT_SECONDS, _error_message, _poll_until_ready


def _latest_projection_date(base_url: str, order_id: str) -> str | None:
    """The most recent projection date that isn't in the future -- not
    just the most recent one that exists. `FineProjectionSummaryService.get_or_schedule` rejects any
    `as_of_date` after today (see InvalidAsOfDateError), and the mock
    scenarios' hardcoded dates (Aug 2026) only sometimes fall entirely
    before "today" depending on when this actually runs -- three of the
    four routinely extend past it. Taking a blind `max()` over all
    history picks a future date for those and 422s every time."""
    resp = httpx.get(f"{base_url}/orders/{order_id}/projections", timeout=30)
    resp.raise_for_status()
    history = resp.json()
    if not history:
        return None
    today = datetime.now(UTC).date().isoformat()
    not_future = [row["projection_date"] for row in history if row["projection_date"] <= today]
    return max(not_future) if not_future else None


def _print_summary(order_id: str, body: dict) -> None:
    print(f"\n=== {order_id} -- as of {body['as_of_date']} (model={body['model_name']}) ===")
    print(body["summary"])


def _summarize_one(base_url: str, order_id: str, force_regenerate: bool) -> None:
    as_of_date = _latest_projection_date(base_url, order_id)
    if as_of_date is None:
        print(
            f"  [skip] {order_id}: no projection exists on or before today -- "
            "run demo_daily_simulation.py first, or this order's whole scenario is still in the future"
        )
        return

    resp = httpx.post(
        f"{base_url}/orders/{order_id}/projection-summary",
        json={"as_of_date": as_of_date, "force_regenerate": force_regenerate},
        timeout=30,
    )
    if resp.status_code == 200:
        # Cache hit -- already the full summary, nothing to schedule or poll.
        _print_summary(order_id, resp.json())
        return
    if resp.status_code != 202:
        print(f"  [failed] {order_id}: {resp.status_code} {_error_message(resp)}", file=sys.stderr)
        return

    job = _poll_until_ready(base_url, order_id, as_of_date)
    if job is None:
        print(
            f"  [timeout] {order_id}: still PENDING after {POLL_TIMEOUT_SECONDS:.0f}s -- try again later",
            file=sys.stderr,
        )
        return
    if job["status"] == "FAILED":
        print(f"  [failed] {order_id}: {job['error_message']}", file=sys.stderr)
        return
    _print_summary(order_id, job["summary"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--order-id", help="Summarize one order only (default: every order on file)")
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="Bypass the persisted-summary cache and call the LLM again",
    )
    args = parser.parse_args()

    if args.order_id:
        order_ids = [args.order_id]
    else:
        resp = httpx.get(f"{args.base_url}/orders", timeout=30)
        resp.raise_for_status()
        order_ids = [o["order_id"] for o in resp.json()]
        if not order_ids:
            print("No orders on file -- run scripts/demo/seed_master_data.py first", file=sys.stderr)
            sys.exit(1)

    for order_id in order_ids:
        _summarize_one(args.base_url, order_id, args.force_regenerate)


if __name__ == "__main__":
    main()
