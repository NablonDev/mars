#!/usr/bin/env python3
"""
No-server CLI: runs the projection service directly against the
configured database (DATABASE_URL / .env), without needing `uvicorn`
running. Kept for ad-hoc/local use and cron/Airflow-style scheduling
where standing up an HTTP server just to run a batch job is unnecessary
-- the API (`POST /api/v1/projections/run`) is the equivalent for
anything that should go through HTTP.

Examples:
    python scripts/run_projection_cli.py --order-id WMT-100234
    python scripts/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05
    python scripts/run_projection_cli.py --all-open
    python scripts/run_projection_cli.py --all-open --date 2026-08-05 --stacking-mode MAX
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.session import Database
from app.repositories.fine_rule_repository import FineRuleRepository
from app.repositories.master_data_repository import MasterDataRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.projection_repository import ProjectionRepository
from app.services.projection_service import ProjectionService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order-id", help="Run a single order by ID")
    parser.add_argument("--all-open", action="store_true", help="Run every order with order_status = OPEN")
    parser.add_argument("--date", help="Projection date, YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--stacking-mode",
        choices=["SUM", "MAX"],
        help="Override the retailer's configured stacking policy for this run only",
    )
    args = parser.parse_args()

    if not args.order_id and not args.all_open:
        parser.error("Pass either --order-id ORDER_ID or --all-open")

    projection_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    database = Database(get_settings().database_url)
    with database.session() as session:
        orders = OrderRepository(session)
        projection_service = ProjectionService(
            orders=orders,
            rules=FineRuleRepository(session),
            master_data=MasterDataRepository(session),
            projections=ProjectionRepository(session),
        )

        if args.order_id:
            order_ids = [args.order_id]
        else:
            order_ids = [o["order_id"] for o in orders.list_orders(order_status="OPEN")]
            print(f"Running {len(order_ids)} open order(s):")

        for order_id in order_ids:
            try:
                result = projection_service.run_for_order(order_id, projection_date, args.stacking_mode)
            # `AppError` covers OrderNotFoundError/NoActiveRulesError;
            # `ValueError` still covers the engine's own rule-data
            # validation, so one bad rule skips one order instead of
            # aborting a whole --all-open batch.
            except (AppError, ValueError) as exc:
                print(f"  [skip] {order_id}: {exc}")
                continue

            parts = ", ".join(
                f"{v.violation_type} {v.probability * 100:.0f}% (${v.expected_fine:,.2f})"
                for v in result.violations
            )
            print(
                f"  {order_id} [{result.projection_date}]  days_to_delivery={result.days_to_delivery:<3} "
                f"stacking={result.stacking_mode:<3} total=${result.total_expected_fine:,.2f}   {parts}"
            )


if __name__ == "__main__":
    main()
