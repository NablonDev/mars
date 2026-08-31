"""
Seeds master/reference data and the four worked-example purchase orders
via the running API -- not by writing to the database directly. This is
the "seed via API" requirement: the only thing this script knows is an
HTTP base URL, exactly what a real ETL job or a teammate with no DB access
would use.

Every response now comes wrapped in the `{success, message, data, error}`
envelope (`app/core/envelope.py`) -- see
`app.schemas.penalties.admin.SeedDataResponse`.

Usage:
    uvicorn app.main:app --reload &        # in one terminal
    python scripts/demo/seed_master_data.py       # in another

    python scripts/demo/seed_master_data.py --base-url http://localhost:8000
    python scripts/demo/seed_master_data.py --force   # truncate + reseed from scratch
"""

import argparse
import sys

import httpx
from _helpers import _auth_headers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Truncate the seeded tables, then reseed from scratch (default: idempotent skip-if-present).",
    )
    args = parser.parse_args()

    resp = httpx.post(
        f"{args.base_url}/admin/seed-master-data",
        params={"force": args.force},
        headers=_auth_headers(),
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Seeding failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)

    counts = resp.json()["data"]
    if args.force:
        print("Master data truncated and reseeded from scratch:")
    else:
        print("Master data seeded (idempotent -- 0s mean it was already there):")
    for key, value in counts.items():
        print(f"  {key:10s}: {value}")


if __name__ == "__main__":
    main()
