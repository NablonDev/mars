"""
No-server CLI: runs the mitigation-ranking service directly against the
configured database (DATABASE_URL / .env), without needing `uvicorn`
running. Kept for ad-hoc/local use and cron/Airflow-style scheduling
where standing up an HTTP server just to run a batch job is unnecessary
-- the API (`POST /orders/{order_id}/mitigation-options`) is the
equivalent for anything that should go through HTTP.

Mitigation never computes a projection of its own: it reads the order's
already-persisted `fines.projected_fine` rows for one date and raises
NO_PROJECTION_EXISTS if that day has none, so run
`scripts/ops/run_projection_cli.py` for the same --date first (see
`docs/RUNBOOK.md` §7, "Mitigation options need a projection for the same
date first"). There is no --stacking-mode here for the same reason: the
projection is a given, and the engine reads the retailer's currently
configured stacking policy rather than accepting a per-run override.

Examples:
    python scripts/ops/run_mitigation_cli.py --order-id WMT-100234
    python scripts/ops/run_mitigation_cli.py --order-id WMT-100234 --date 2026-08-05
    python scripts/ops/run_mitigation_cli.py --all-open
    python scripts/ops/run_mitigation_cli.py --all-open --date 2026-08-05
    python scripts/ops/run_mitigation_cli.py --all-open --with-summary

--with-summary additionally runs the fine-mitigation-summary generation
for each order right after its mitigation ranking succeeds -- the same
sequential guarantee as `POST /orders/{order_id}/mitigation-options/runs`, for this
no-HTTP-server path. Runs inline (no BackgroundTasks needed in a
one-shot CLI process) and needs AZURE_OPENAI_* configured.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import LLMConfig, get_settings
from app.core.exceptions import AppError
from app.db.session import Database
from app.models.enums import SummaryStatus
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_mitigation.mitigation import MitigationRepository, MitigationResultRepository
from app.repositories.fine_mitigation.summary import FineMitigationSummaryRepository
from app.repositories.fine_projection.projection import ProjectionRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.order import OrderRepository
from app.services.fine_mitigation.service import FineMitigationService
from app.services.fine_mitigation.summary import FineMitigationSummaryService

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order-id", help="Run a single order by ID")
    parser.add_argument("--all-open", action="store_true", help="Run every order with order_status = OPEN")
    parser.add_argument("--date", help="Projection date to rank against, YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--with-summary",
        action="store_true",
        help="Also generate the fine mitigation summary for each order after a successful ranking",
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
        mitigation_results = MitigationResultRepository(session)
        mitigation_service = FineMitigationService(
            orders=orders,
            rules=rules,
            master_data=master_data,
            projections=projections,
            mitigation_inputs=MitigationRepository(session),
            mitigation_results=mitigation_results,
        )
        fine_mitigation_summary_service = None
        if args.with_summary:
            fine_mitigation_summary_service = FineMitigationSummaryService(
                orders=orders,
                master_data=master_data,
                mitigation_results=mitigation_results,
                summaries=FineMitigationSummaryRepository(session),
                prompt_registry=PromptRegistryRepository(session),
                llm=AzureOpenAIChatClient(
                    LLMConfig.from_settings(settings),
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
                run_date, options = mitigation_service.run_for_order(order_id, projection_date)
            # `AppError` covers OrderNotFoundError/NoProjectionExistsError;
            # `ValueError` still covers the engine's own input validation,
            # so one bad order skips one order instead of aborting a whole
            # --all-open batch.
            except (AppError, ValueError) as exc:
                print(f"  [skip] {order_id}: {exc}")
                continue

            best = options[0] if options else None
            parts = ", ".join(
                f"{o.action} net=${o.net_saving:,.2f} ({o.risk_level}/{o.confidence})" for o in options
            )
            print(
                f"  {order_id} [{run_date}]  options={len(options):<3} "
                f"best={best.action if best else '-':<20} "
                f"net_saving=${best.net_saving if best else 0.0:,.2f}   {parts}"
            )

            if fine_mitigation_summary_service is None:
                continue

            try:
                job = fine_mitigation_summary_service.get_or_schedule(order_id, as_of_date=run_date)
                if job.status == SummaryStatus.PENDING:
                    try:
                        fine_mitigation_summary_service.run_generation(
                            job.order_id, job.as_of_date, job.prompt_version
                        )
                    except Exception:
                        # run_generation re-raises after persisting a
                        # FAILED ledger row (see
                        # FineMitigationSummaryService.run_generation) so a
                        # queue worker can classify retry-vs-dead. This
                        # one-shot CLI has always printed the resulting
                        # status line below regardless of success or
                        # failure -- get_status reads that same ledger
                        # row -- so swallow here rather than falling into
                        # the `except AppError` below, which prints a
                        # different "[summary skipped]" message reserved
                        # for get_or_schedule failing outright.
                        logger.exception(
                            "Fine mitigation summary generation failed: order_id=%s as_of_date=%s",
                            job.order_id,
                            job.as_of_date,
                        )
                    job = fine_mitigation_summary_service.get_status(order_id, as_of_date=run_date)
            except AppError as exc:
                print(f"    [summary skipped] {order_id}: {exc}")
                continue

            print(f"    summary [{job.status}]")


if __name__ == "__main__":
    main()
