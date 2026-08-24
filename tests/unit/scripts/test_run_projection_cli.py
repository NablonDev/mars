"""Regression test for scripts/ops/run_projection_cli.py's fine-projection-summary guard.

FineProjectionSummaryService.run_generation now re-raises after persisting a FAILED
ledger row (see tests/services/test_fine_projection_summary.py for the service-level
coverage). This CLI has always printed the resulting `summary [STATUS]`
line regardless of success or failure, reading it back via get_status --
that must still be true, and a run_generation failure must NOT be
misrouted into the `except AppError` branch, which prints a different
"[summary skipped]" message reserved for get_or_schedule failing outright.
"""

from datetime import date

from sqlalchemy.pool import StaticPool

import scripts.ops.run_projection_cli as cli
from app.db.session import Database
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.order import OrderRepository


class _FailingChatClient:
    model_name = "fake-model"

    def __init__(self, *args, **kwargs) -> None:
        pass

    def invoke(self, messages, *, tools=None):
        raise RuntimeError("simulated upstream failure")


def _seed_order(database: Database) -> None:
    with database.session() as session:
        master_data = MasterDataRepository(session)
        rules = FineRuleRepository(session)
        orders = OrderRepository(session)

        master_data.add_retailer("RET-CLI", "Retailer CLI", None, "SUM")
        master_data.add_sku("SKU-CLI", "MAT-CLI", None)
        master_data.add_location("LOC-CLI", None, None)
        orders.create_order(
            order_id="ORD-CLI",
            retailer_id="RET-CLI",
            sku_id="SKU-CLI",
            ship_from_location_id="LOC-CLI",
            order_qty=100,
            unit_price=5.0,
            order_date=date(2026, 8, 1),
            requested_delivery_date=date(2026, 8, 10),
            required_ship_date=date(2026, 8, 8),
        )
        rules.add_rule(
            rule_id="RULE-CLI-FLAT",
            retailer_id="RET-CLI",
            violation_type="OTIF_LATE",
            calc_type="FLAT_FEE",
            rate=50.0,
        )


def test_run_generation_failure_still_prints_the_status_line(monkeypatch, capsys):
    database = Database("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.create_all_tables()
    _seed_order(database)

    monkeypatch.setattr(cli, "Database", lambda *_args, **_kwargs: database)
    monkeypatch.setattr(cli, "AzureOpenAIChatClient", _FailingChatClient)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_projection_cli.py",
            "--order-id",
            "ORD-CLI",
            "--date",
            "2026-08-05",
            "--with-summary",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "summary [FAILED]" in out
    assert "[summary skipped]" not in out


def test_run_generation_success_still_prints_the_status_line(monkeypatch, capsys):
    """Same guard, success path: unaffected by the try/except added around
    run_generation."""

    class _SucceedingChatClient:
        model_name = "fake-model"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def invoke(self, messages, *, tools=None):
            from types import SimpleNamespace

            return SimpleNamespace(content="All clear.", tool_calls=[])

    database = Database("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.create_all_tables()
    _seed_order(database)

    monkeypatch.setattr(cli, "Database", lambda *_args, **_kwargs: database)
    monkeypatch.setattr(cli, "AzureOpenAIChatClient", _SucceedingChatClient)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_projection_cli.py",
            "--order-id",
            "ORD-CLI",
            "--date",
            "2026-08-05",
            "--with-summary",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "summary [READY]" in out
    assert "[summary skipped]" not in out
