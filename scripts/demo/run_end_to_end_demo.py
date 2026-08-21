"""
One-command tour of the whole system: seeds master data, replays all four
worked-example scenarios day by day, then asks the LLM-powered
fine-summary endpoint to summarize *why* each order's final number is what
it is -- deterministic engine output feeding a natural-language layer,
in one run, from a clean DB to a client-readable paragraph.

Not a new capability -- it is `seed_master_data.py` +
`demo_daily_simulation.py` + `demo_fine_summary.py` chained together, using
each scenario's own last simulated day (taken straight from the
simulate-daily-run response) as that order's `as_of_date`, so it needs no
separate discovery call per order and can't drift out of the range
`FineSummaryService.get_or_schedule` accepts. Prefer this script for a
from-scratch demo; use the three individual scripts when you want to run
or inspect one stage on its own (e.g. a bare engine replay with no LLM
cost, or re-summarizing one order without re-seeding).

Fine-summary generation is a background job: a cache miss (or
--force-regenerate) gets a `202` immediately, not a `200` with the
summary already in it -- this script polls `GET .../summary` until
each job leaves `PENDING`, per `docs/API.md` "Fine Summaries."

The last stage needs real Azure OpenAI credentials in `.env`
(AZURE_OPENAI_API_KEY/ENDPOINT/DEPLOYMENT_NAME) -- without them this still
seeds and simulates successfully and just prints "Fine summary generation
failed upstream" for each order instead of failing the whole run. Pass
--skip-fine-summary to stop after the deterministic stage on purpose.

Usage:
    uvicorn app.main:app --reload &
    python scripts/demo/run_end_to_end_demo.py
    python scripts/demo/run_end_to_end_demo.py --base-url http://localhost:9000/api/v1
    python scripts/demo/run_end_to_end_demo.py --skip-fine-summary
"""

import argparse
import sys
from datetime import UTC, datetime

import httpx
from _helpers import POLL_TIMEOUT_SECONDS, _error_message, _poll_until_ready


def _seed(base_url: str) -> None:
    print("== Seeding master data ==")
    resp = httpx.post(f"{base_url}/admin/seed-master-data", timeout=30)
    if resp.status_code != 200:
        print(f"Seeding failed: {resp.status_code} {_error_message(resp)}", file=sys.stderr)
        sys.exit(1)
    print("  (idempotent -- 0s below mean it was already there)")
    for key, value in resp.json().items():
        print(f"  {key:10s}: {value}")


def _simulate(base_url: str) -> list[dict]:
    print("\n== Replaying daily scenarios ==")
    resp = httpx.post(f"{base_url}/admin/simulate-daily-run", timeout=60)
    if resp.status_code != 200:
        print(f"Simulation failed: {resp.status_code} {_error_message(resp)}", file=sys.stderr)
        sys.exit(1)
    scenarios = resp.json()["scenarios"]
    for scenario in scenarios:
        last_day = scenario["days"][-1]
        print(
            f"  {scenario['order_id']}: {len(scenario['days'])} days simulated, "
            f"final total=${last_day['total_expected_fine']:,.2f} on {last_day['projection_date']}"
        )
    return scenarios


def _summarize_all(base_url: str, scenarios: list[dict], force_regenerate: bool) -> None:
    print("\n== Summarizing each order's final number ==")
    today = datetime.now(UTC).date().isoformat()
    for scenario in scenarios:
        order_id = scenario["order_id"]
        # The scenario's *last* simulated day, not necessarily today's:
        # the mock scenarios' hardcoded dates (Aug 2026) routinely extend
        # past the real calendar date, and get_or_schedule rejects any
        # `as_of_date` after today. Clamp to the latest day that isn't in
        # the future rather than assuming the last simulated day always is.
        not_future = [d["projection_date"] for d in scenario["days"] if d["projection_date"] <= today]
        if not not_future:
            print(f"\n  [skip] {order_id}: this scenario's whole timeline is still in the future")
            continue
        as_of_date = max(not_future)
        resp = httpx.post(
            f"{base_url}/orders/{order_id}/summary",
            json={"as_of_date": as_of_date, "force_regenerate": force_regenerate},
            timeout=30,
        )
        if resp.status_code == 200:
            body = resp.json()
        elif resp.status_code == 202:
            job = _poll_until_ready(base_url, order_id, as_of_date)
            if job is None:
                print(
                    f"\n  [timeout] {order_id}: still PENDING after "
                    f"{POLL_TIMEOUT_SECONDS:.0f}s -- try again later",
                    file=sys.stderr,
                )
                continue
            if job["status"] == "FAILED":
                print(f"\n  [failed] {order_id}: {job['error_message']}", file=sys.stderr)
                continue
            body = job["summary"]
        else:
            print(f"\n  [failed] {order_id}: {resp.status_code} {_error_message(resp)}", file=sys.stderr)
            continue

        print(f"\n  {order_id} (as of {as_of_date}):")
        print(f"    {body['summary']}")

    print("\nFull per-order summary text: python scripts/demo/demo_fine_summary.py")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument(
        "--skip-fine-summary",
        action="store_true",
        help="Stop after the deterministic engine stage -- no LLM calls, no Azure OpenAI credentials needed",
    )
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="Bypass the persisted-summary cache and call the LLM again for every order",
    )
    args = parser.parse_args()

    _seed(args.base_url)
    scenarios = _simulate(args.base_url)
    if not args.skip_fine_summary:
        _summarize_all(args.base_url, scenarios, args.force_regenerate)


if __name__ == "__main__":
    main()
