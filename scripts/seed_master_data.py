#!/usr/bin/env python3
"""
Seeds master/reference data and the four worked-example orders via the
running API -- not by writing to the database directly. This is the
"seed via API" requirement: the only thing this script knows is an HTTP
base URL, exactly what a real ETL job or a teammate with no DB access
would use.

Usage:
    uvicorn app.main:app --reload &        # in one terminal
    python scripts/seed_master_data.py       # in another

    python scripts/seed_master_data.py --base-url http://localhost:8000
"""

import argparse
import sys

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    args = parser.parse_args()

    resp = httpx.post(f"{args.base_url}/admin/seed-master-data", timeout=30)
    if resp.status_code != 200:
        print(f"Seeding failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)

    counts = resp.json()
    print("Master data seeded (idempotent -- 0s mean it was already there):")
    for key, value in counts.items():
        print(f"  {key:10s}: {value}")


if __name__ == "__main__":
    main()
