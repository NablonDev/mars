#!/usr/bin/env python3
"""
Calls the LLM-powered projection-explanation endpoint
(`POST /orders/{order_id}/explanation`) for one or every order and prints
the structured result as readable text -- the "why is this order's number
what it is" companion to `demo_daily_simulation.py`'s "what is the
number."

Requires real Azure OpenAI credentials in `.env`
(AZURE_OPENAI_API_KEY/ENDPOINT/DEPLOYMENT_NAME) -- without them the
endpoint 502s with a clear "Explanation generation failed upstream"
message (see app/services/explanation_service.py), which this script
prints per-order and moves on rather than treating as a script bug.

For each order this explicitly looks up its latest existing projection
date via `GET /orders/{id}/projections` and passes that as `as_of_date`
-- it never omits `as_of_date` and relies on the server's
"defaults to today" behaviour, since that only happens to produce a
sensible answer while the mock scenario dates (Aug 2026) and the real
calendar date coincide. Explaining an order with no projections yet is a
clean skip, not a crash -- run `demo_daily_simulation.py` (or
`projections/run`) first.

Usage:
    uvicorn app.main:app --reload &
    python scripts/seed_master_data.py
    python scripts/demo_daily_simulation.py

    python scripts/demo_explanation.py                              # every order on file
    python scripts/demo_explanation.py --order-id WMT-100234         # one order
    python scripts/demo_explanation.py --order-id WMT-100234 --force-regenerate
"""

import argparse
import sys
from datetime import UTC, datetime

import httpx


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


def _latest_projection_date(base_url: str, order_id: str) -> str | None:
    """The most recent projection date that isn't in the future -- not
    just the most recent one that exists. `explain_order` rejects any
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


def _print_explanation(order_id: str, exp: dict) -> None:
    print(f"\n=== {order_id} -- as of {exp['as_of_date']} ===")
    print(f"  {exp['headline_summary']}")
    print(
        f"  Total expected fine: ${exp['current_total_expected_fine']:,.2f}  (stacking={exp['stacking_mode']})"
    )

    if exp["violations"]:
        print("  Violations:")
        for v in exp["violations"]:
            print(
                f"    - {v['violation_type']} ({v['rule_id']}): "
                f"{v['probability'] * 100:.0f}% x ${v['fine_if_realized']:,.2f} "
                f"= ${v['expected_fine']:,.2f}"
            )
            print(f"      {v['explanation']}")

    if exp["day_by_day_narrative"]:
        print("  Day-by-day:")
        for day in exp["day_by_day_narrative"]:
            print(
                f"    {day['projection_date']}  ${day['total_expected_fine']:>8,.2f}   {day['change_summary']}"
            )

    if exp["key_sensitivity_factors"]:
        print("  Sensitive to:")
        for factor in exp["key_sensitivity_factors"]:
            print(f"    - {factor}")

    if exp["caveats"]:
        print("  Caveats:")
        for c in exp["caveats"]:
            print(f"    [{c['kind']}] {c['message']}")


def _explain_one(base_url: str, order_id: str, force_regenerate: bool) -> None:
    as_of_date = _latest_projection_date(base_url, order_id)
    if as_of_date is None:
        print(
            f"  [skip] {order_id}: no projection exists on or before today -- "
            "run demo_daily_simulation.py first, or this order's whole scenario is still in the future"
        )
        return

    resp = httpx.post(
        f"{base_url}/orders/{order_id}/explanation",
        json={"as_of_date": as_of_date, "force_regenerate": force_regenerate},
        timeout=60,
    )
    if resp.status_code != 200:
        print(f"  [failed] {order_id}: {resp.status_code} {_error_message(resp)}", file=sys.stderr)
        return
    _print_explanation(order_id, resp.json())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--order-id", help="Explain one order only (default: every order on file)")
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="Bypass the persisted-explanation cache and call the LLM again",
    )
    args = parser.parse_args()

    if args.order_id:
        order_ids = [args.order_id]
    else:
        resp = httpx.get(f"{args.base_url}/orders", timeout=30)
        resp.raise_for_status()
        order_ids = [o["order_id"] for o in resp.json()]
        if not order_ids:
            print("No orders on file -- run scripts/seed_master_data.py first", file=sys.stderr)
            sys.exit(1)

    for order_id in order_ids:
        _explain_one(args.base_url, order_id, args.force_regenerate)


if __name__ == "__main__":
    main()
