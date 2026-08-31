"""
Calls the LLM-powered penalty-mitigation-summary endpoint
(`POST /purchase-orders/{purchase_order_id}/penalty-mitigations/summary`)
for one or every purchase order and prints the free-text result -- the
"which action should we take, and why" companion to
`demo_penalty_projection_summary.py`'s "why is this order's number what
it is."

Was `demo_fine_mitigation_summary.py` (`fine`/`fines` -> `penalty`/
`penalties` rename; `/orders/{id}/mitigation-options` ->
`/penalty-mitigations?projection_id=...`, per the approved plan §5 --
**a real capability change, not just a rename**: mitigation options are
now keyed by a specific `penalty_projection` row's own surrogate id, not
just a `(purchase_order, date)` pair, so this script first resolves that
id via `GET /purchase-orders/{id}/penalty-exposure`'s latest violation
row). Every response now comes wrapped in the `{success, message, data,
error}` envelope (`app/core/envelope.py`) -- every `resp.json()` below
reads `["data"]`.

Ranked mitigation options are the prerequisite here, not projections
directly -- generating a summary needs `penalties.mitigation_option` rows
to already exist for the resolved projection, and raises
`NO_MITIGATION_OPTIONS_EXIST` (`422`, `BusinessRuleError`) for a purchase
order that has none. Those in turn need a projection for the same date
first, so the full prerequisite chain is
projections -> mitigation-options -> this script -- run
`scripts/ops/run_mitigation_cli.py --purchase-order-id <id>` (or
`POST /penalty-mitigations?projection_id=...`) first.

Generation is a background job: a cache miss (or --force-regenerate) gets
a `202` immediately. Unlike the pre-restructure API, there is no
per-(purchase_order, date) summary-poll endpoint -- `?include=summary` is
nested under one specific mitigation option's own resource
(`GET /penalty-mitigations/{mitigation_id}?include=summary`), so this
script polls that. That row only ever changes once a worker actually
drains the queued regeneration job -- run
`python scripts/ops/run_daily_batch.py --drain-only` (or the full nightly
batch) in another terminal alongside this script; before this pass no
worker existed to do that at all (see `app.workers.penalty_mitigation`).

Requires real Azure OpenAI credentials in `.env`
(AZURE_OPENAI_API_KEY/ENDPOINT/DEPLOYMENT_NAME) -- without them the background job
lands on a FAILED status with a clear "Penalty mitigation summary
generation failed upstream" message (see
app/services/penalties/mitigation/summary_service.py), which this script
prints per purchase order and moves on rather than treating as a script
bug.

Usage:
    uvicorn app.main:app --reload &
    python scripts/demo/seed_master_data.py
    python scripts/demo/demo_daily_simulation.py
    python scripts/ops/run_mitigation_cli.py --all-open
    python scripts/ops/run_daily_batch.py --drain-only &   # drains queued summary jobs

    python scripts/demo/demo_penalty_mitigation_summary.py                              # every purchase order on file
    python scripts/demo/demo_penalty_mitigation_summary.py --purchase-order-id <uuid>    # one purchase order
    python scripts/demo/demo_penalty_mitigation_summary.py --purchase-order-id <uuid> --force-regenerate
"""

import argparse
import sys
import time

import httpx
from _helpers import POLL_INTERVAL_SECONDS, POLL_TIMEOUT_SECONDS, _auth_headers, _error_message


def _latest_projection_id_and_date(base_url: str, purchase_order_id: str) -> tuple[str, str] | None:
    """The purchase order's latest `penalty_projection` row id and date, via
    its one violation row (any one -- they all share the same
    `projection_date`), or None if no projection has ever run, or it ran
    with zero violations (nothing to mitigate)."""
    resp = httpx.get(
        f"{base_url}/purchase-orders/{purchase_order_id}/penalty-exposure",
        headers=_auth_headers(),
        timeout=30,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.json()["data"]
    violations = body["violations"]
    if not violations:
        return None
    return violations[0]["id"], body["projection_date"]


def _latest_mitigation_id(base_url: str, projection_id: str) -> str | None:
    resp = httpx.get(
        f"{base_url}/penalty-mitigations",
        params={"projection_id": projection_id},
        headers=_auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    options = resp.json()["data"]["options"]
    return options[0]["id"] if options else None


def _poll_until_ready(base_url: str, mitigation_id: str) -> dict | None:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    headers = _auth_headers()
    while time.monotonic() < deadline:
        resp = httpx.get(
            f"{base_url}/penalty-mitigations/{mitigation_id}",
            params={"include": "summary"},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        row = resp.json()["data"]
        if row["summary_status"] not in (None, "PENDING"):
            return row
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _print_summary(purchase_order_id: str, body: dict) -> None:
    print(f"\n=== {purchase_order_id} -- as of {body['as_of_date']} (model={body['model_name']}) ===")
    print(body["summary"])


def _summarize_one(base_url: str, purchase_order_id: str, force_regenerate: bool) -> None:
    resolved = _latest_projection_id_and_date(base_url, purchase_order_id)
    if resolved is None:
        print(
            f"  [skip] {purchase_order_id}: no penalty exposure on file -- "
            "run demo_daily_simulation.py first, or this purchase order has no violations to mitigate"
        )
        return
    projection_id, as_of_date = resolved

    mitigation_id = _latest_mitigation_id(base_url, projection_id)
    if mitigation_id is None:
        print(
            f"  [skip] {purchase_order_id}: no mitigation options exist yet -- "
            "run scripts/ops/run_mitigation_cli.py first"
        )
        return

    resp = httpx.post(
        f"{base_url}/purchase-orders/{purchase_order_id}/penalty-mitigations/summary",
        json={"as_of_date": as_of_date, "force_regenerate": force_regenerate},
        headers=_auth_headers(),
        timeout=30,
    )
    if resp.status_code == 200:
        # Cache hit -- already the full summary, nothing to schedule or poll.
        _print_summary(purchase_order_id, resp.json()["data"]["summary"])
        return
    if resp.status_code != 202:
        print(f"  [failed] {purchase_order_id}: {resp.status_code} {_error_message(resp)}", file=sys.stderr)
        return

    row = _poll_until_ready(base_url, mitigation_id)
    if row is None:
        print(
            f"  [timeout] {purchase_order_id}: still PENDING after {POLL_TIMEOUT_SECONDS:.0f}s -- "
            "is a worker draining the queue? (python scripts/ops/run_daily_batch.py --drain-only)",
            file=sys.stderr,
        )
        return
    if row["summary_status"] == "FAILED":
        print(f"  [failed] {purchase_order_id}: generation failed upstream", file=sys.stderr)
        return
    _print_summary(purchase_order_id, row["summary"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument(
        "--purchase-order-id", help="Summarize one purchase order only (default: every one on file)"
    )
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="Bypass the persisted-summary cache and call the LLM again",
    )
    args = parser.parse_args()

    if args.purchase_order_id:
        purchase_order_ids = [args.purchase_order_id]
    else:
        resp = httpx.get(f"{args.base_url}/purchase-orders", headers=_auth_headers(), timeout=30)
        resp.raise_for_status()
        purchase_order_ids = [po["id"] for po in resp.json()["data"]]
        if not purchase_order_ids:
            print("No purchase orders on file -- run scripts/demo/seed_master_data.py first", file=sys.stderr)
            sys.exit(1)

    for purchase_order_id in purchase_order_ids:
        _summarize_one(args.base_url, purchase_order_id, args.force_regenerate)


if __name__ == "__main__":
    main()
