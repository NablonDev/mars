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
    python scripts/run_projection_cli.py --all-open --with-summary

--with-summary additionally runs the fine-summary generation for each
order right after its projection succeeds -- the same sequential
guarantee as `POST /orders/{order_id}/run`, for this no-HTTP-server path.
Runs inline (no BackgroundTasks needed in a one-shot CLI process) and
needs AZURE_OPENAI_* configured; see docs/scheduling-options.md for how
this script is meant to be invoked on a schedule.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.session import Database
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.master_data import MasterDataRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.services.fine_summary import FineSummaryService
from app.services.projection import ProjectionService


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
    parser.add_argument(
        "--with-summary",
        action="store_true",
        help="Also generate the fine summary for each order after a successful projection",
    )
    args = parser.parse_args()

    if not args.order_id and not args.all_open:
        parser.error("Pass either --order-id ORDER_ID or --all-open")

    projection_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None  # noqa: DTZ007

    settings = get_settings()
    database = Database(settings.database_url)
    with database.session() as session:
        orders = OrderRepository(session)
        rules = FineRuleRepository(session)
        master_data = MasterDataRepository(session)
        projections = ProjectionRepository(session)
        projection_service = ProjectionService(
            orders=orders,
            rules=rules,
            master_data=master_data,
            projections=projections,
        )
        fine_summary_service = None
        if args.with_summary:
            fine_summary_service = FineSummaryService(
                orders=orders,
                rules=rules,
                master_data=master_data,
                projections=projections,
                summaries=FineSummaryRepository(session),
                prompt_registry=PromptRegistryRepository(session),
                llm=AzureOpenAIChatClient(
                    settings=settings,
                    max_retries=settings.azure_openai_max_attempts,
                    timeout_seconds=settings.azure_openai_timeout_seconds,
                ),
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

            if fine_summary_service is None:
                continue

            try:
                job = fine_summary_service.get_or_schedule(order_id, as_of_date=result.projection_date)
                if job.status == "PENDING":
                    fine_summary_service.run_generation(job.order_id, job.as_of_date, job.prompt_version)
                    job = fine_summary_service.get_status(order_id, as_of_date=result.projection_date)
            except AppError as exc:
                print(f"    [summary skipped] {order_id}: {exc}")
                continue

            print(f"    summary [{job.status}]")


if __name__ == "__main__":
    main()
