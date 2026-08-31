"""
Replays all four worked-example scenarios day by day through the running
API and prints the same trace `simulate_daily_run.py` used to print
directly against SQLAlchemy sessions -- now going through
HTTP -> router -> service -> repository -> DB instead.

One HTTP call (`POST /admin/simulate-daily-run`) does the actual
day-by-day fact-writing and projecting server-side, inside one service
method (app/services/seeding/service.py::PenaltySeedingService.
simulate_daily_run) -- keeping that sequence atomic and consistent per
purchase order was more important than exposing per-day granularity over
HTTP for a demo script. This script's job is just to call it and render
the day-by-day trace from the response, which already carries every
day's numbers.

Every response now comes wrapped in the `{success, message, data, error}`
envelope (`app/core/envelope.py`), and each scenario is keyed by
`purchase_order_id` (was `order_id`) -- see
`app.schemas.penalties.admin.ScenarioSummary`.

Usage:
    uvicorn app.main:app --reload &
    python scripts/demo/seed_master_data.py
    python scripts/demo/demo_daily_simulation.py
"""

import argparse
import sys

import httpx
from _helpers import _auth_headers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    args = parser.parse_args()

    resp = httpx.post(
        f"{args.base_url}/admin/simulate-daily-run",
        headers=_auth_headers(),
        timeout=60,
    )
    if resp.status_code != 200:
        print(f"Simulation failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)

    for scenario in resp.json()["data"]["scenarios"]:
        print(f"\n=== {scenario['purchase_order_id']} -- day-by-day projection trend ===")
        for day in scenario["days"]:
            print(
                f"  {day['projection_date']}  "
                f"shortage={day['shortage_probability'] * 100:5.1f}% (${day['shortage_penalty']:>7,.2f})  "
                f"delay={day['delay_probability'] * 100:5.1f}% (${day['delay_penalty']:>7,.2f})  "
                f"total=${day['total_expected_penalty']:>8,.2f}   {day['note']}"
            )


if __name__ == "__main__":
    main()
