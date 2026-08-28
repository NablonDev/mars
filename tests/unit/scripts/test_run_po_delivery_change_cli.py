"""Tests for scripts/ops/run_po_delivery_change_cli.py's four subcommands
(create/respond/history/expire-sweep) -- exit codes, clean (non-traceback)
error output on the domain exceptions, and output content. Mirrors
tests/unit/services/test_po_delivery_change_request_service.py's seeding
(dates computed relative to date.today(), not hardcoded) and
tests/unit/scripts/test_run_daily_batch.py's `_invoke_main` pattern for
asserting on `main()`'s exit code via `sys.exit(main())`.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.pool import StaticPool

import scripts.ops.run_po_delivery_change_cli as cli
from app.db.session import Database
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_projection.po_delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.order import OrderRepository

_ORDER_QTY = 1000
_UNIT_PRICE = 10.0
_TODAY = date.today()  # noqa: DTZ011 -- test date anchor, same convention as the service test module


def _invoke_main() -> int:
    """Mirrors what `sys.exit(main())` actually does at the OS level -- see
    tests/unit/scripts/test_run_daily_batch.py's `_invoke_main` for the full
    rationale."""
    try:
        return cli.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException:  # noqa: BLE001 -- deliberately mirrors Python's own default excepthook
        return 1


def _make_database() -> Database:
    database = Database("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.create_all_tables()
    return database


def _seed_order(
    database: Database,
    order_id: str,
    *,
    required_ship_date: date,
    requested_delivery_date: date,
    retailer_id: str = "RET-CLI-EXT",
    extension_min_lead_days: int = 2,
    extension_response_sla_hours: int = 48,
) -> None:
    sku_id = f"SKU-{retailer_id}"
    location_id = f"LOC-{retailer_id}"
    with database.session() as session:
        master_data = MasterDataRepository(session)
        rules = FineRuleRepository(session)
        orders = OrderRepository(session)

        master_data.add_retailer(
            retailer_id,
            "CLI Extension Test Retailer",
            None,
            "SUM",
            extension_min_lead_days,
            extension_response_sla_hours,
            0.0,
        )
        master_data.add_sku(sku_id, "MAT-CLI-EXT", None)
        master_data.add_location(location_id, None, None)
        rules.add_rule(
            rule_id=f"RULE-{order_id}",
            retailer_id=retailer_id,
            violation_type="OTIF_LATE",
            calc_type="FLAT_FEE",
            rate=25.0,
        )
        orders.create_order(
            order_id=order_id,
            retailer_id=retailer_id,
            sku_id=sku_id,
            ship_from_location_id=location_id,
            order_qty=_ORDER_QTY,
            unit_price=_UNIT_PRICE,
            order_date=_TODAY,
            requested_delivery_date=requested_delivery_date,
            required_ship_date=required_ship_date,
        )


def _create_request(
    database: Database, order_id: str, reason_code: str, proposed_delivery_date: date
) -> dict:
    with database.session() as session:
        service = cli._build_service(session)
        return service.create_request(order_id, reason_code, proposed_delivery_date)


def test_create_succeeds_and_prints_request(monkeypatch, capsys):
    database = _make_database()
    _seed_order(
        database,
        "ORD-CLI-1",
        required_ship_date=_TODAY + timedelta(days=10),
        requested_delivery_date=_TODAY + timedelta(days=12),
    )
    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_po_delivery_change_cli.py",
            "create",
            "--order-id",
            "ORD-CLI-1",
            "--reason-code",
            "SHORTAGE",
            "--proposed-delivery-date",
            (_TODAY + timedelta(days=16)).isoformat(),
        ],
    )

    assert _invoke_main() == 0

    out = capsys.readouterr().out
    assert "Created ext_" in out
    assert "ORD-CLI-1" in out
    assert "SHORTAGE" in out
    assert "Traceback" not in out


def test_create_insufficient_lead_time_exits_nonzero_without_traceback(monkeypatch, capsys):
    database = _make_database()
    _seed_order(
        database,
        "ORD-CLI-2",
        required_ship_date=_TODAY,  # 0 days lead, default extension_min_lead_days=2
        requested_delivery_date=_TODAY + timedelta(days=2),
    )
    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_po_delivery_change_cli.py",
            "create",
            "--order-id",
            "ORD-CLI-2",
            "--reason-code",
            "SHORTAGE",
            "--proposed-delivery-date",
            (_TODAY + timedelta(days=6)).isoformat(),
        ],
    )

    assert _invoke_main() != 0

    out = capsys.readouterr().out
    assert out.startswith("Error:")
    assert "Traceback" not in out


def test_respond_accepted_updates_order_dates(monkeypatch, capsys):
    database = _make_database()
    _seed_order(
        database,
        "ORD-CLI-3",
        required_ship_date=_TODAY + timedelta(days=10),
        requested_delivery_date=_TODAY + timedelta(days=12),
        retailer_id="RET-CLI-3",
    )
    request = _create_request(database, "ORD-CLI-3", "DELAY", _TODAY + timedelta(days=16))

    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_po_delivery_change_cli.py",
            "respond",
            "--request-id",
            request["request_id"],
            "--decision",
            "ACCEPTED",
        ],
    )

    assert _invoke_main() == 0
    out = capsys.readouterr().out
    assert "Recorded ACCEPTED" in out
    assert request["request_id"] in out


def test_respond_countered_updates_order_dates(monkeypatch, capsys):
    database = _make_database()
    _seed_order(
        database,
        "ORD-CLI-4",
        required_ship_date=_TODAY + timedelta(days=10),
        requested_delivery_date=_TODAY + timedelta(days=12),
        retailer_id="RET-CLI-4",
    )
    request = _create_request(database, "ORD-CLI-4", "DELAY", _TODAY + timedelta(days=16))

    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_po_delivery_change_cli.py",
            "respond",
            "--request-id",
            request["request_id"],
            "--decision",
            "COUNTERED",
            "--countered-delivery-date",
            (_TODAY + timedelta(days=14)).isoformat(),
        ],
    )

    assert _invoke_main() == 0
    out = capsys.readouterr().out
    assert "Recorded COUNTERED" in out
    assert "countered=" in out


def test_respond_rejected_leaves_order_untouched(monkeypatch, capsys):
    database = _make_database()
    _seed_order(
        database,
        "ORD-CLI-5",
        required_ship_date=_TODAY + timedelta(days=10),
        requested_delivery_date=_TODAY + timedelta(days=12),
        retailer_id="RET-CLI-5",
    )
    request = _create_request(database, "ORD-CLI-5", "SHORTAGE", _TODAY + timedelta(days=16))

    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_po_delivery_change_cli.py",
            "respond",
            "--request-id",
            request["request_id"],
            "--decision",
            "REJECTED",
        ],
    )

    assert _invoke_main() == 0
    out = capsys.readouterr().out
    assert "Recorded REJECTED" in out


def test_respond_countered_missing_countered_date_errors_cleanly(monkeypatch, capsys):
    database = _make_database()
    _seed_order(
        database,
        "ORD-CLI-6",
        required_ship_date=_TODAY + timedelta(days=10),
        requested_delivery_date=_TODAY + timedelta(days=12),
        retailer_id="RET-CLI-6",
    )
    request = _create_request(database, "ORD-CLI-6", "DELAY", _TODAY + timedelta(days=16))

    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_po_delivery_change_cli.py",
            "respond",
            "--request-id",
            request["request_id"],
            "--decision",
            "COUNTERED",
        ],
    )

    assert _invoke_main() != 0
    out = capsys.readouterr().out
    assert out.startswith("Error:")
    assert "countered_delivery_date" in out
    assert "Traceback" not in out


def test_history_lists_in_chronological_order(monkeypatch, capsys):
    database = _make_database()
    _seed_order(
        database,
        "ORD-CLI-7",
        required_ship_date=_TODAY + timedelta(days=10),
        requested_delivery_date=_TODAY + timedelta(days=12),
        retailer_id="RET-CLI-7",
    )
    first = _create_request(database, "ORD-CLI-7", "DELAY", _TODAY + timedelta(days=16))
    with database.session() as session:
        po_delivery_change_requests = PoDeliveryChangeRequestRepository(session)
        service = cli._build_service(session)
        service.record_response(first["request_id"], "REJECTED")
    second = _create_request(database, "ORD-CLI-7", "SHORTAGE", _TODAY + timedelta(days=17))

    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        ["run_po_delivery_change_cli.py", "history", "--order-id", "ORD-CLI-7"],
    )

    assert _invoke_main() == 0
    out = capsys.readouterr().out
    assert out.index(first["request_id"]) < out.index(second["request_id"])

    # Confirm against the repository directly, too -- list_history's own
    # ordering contract is already covered by the service-level tests, this
    # asserts the CLI didn't reorder what it got back.
    with database.session() as session:
        history = po_delivery_change_requests.list_history("ORD-CLI-7")
    assert [row["request_id"] for row in history] == [first["request_id"], second["request_id"]]


def test_history_unknown_order_errors_cleanly(monkeypatch, capsys):
    database = _make_database()
    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        ["run_po_delivery_change_cli.py", "history", "--order-id", "does-not-exist"],
    )

    assert _invoke_main() != 0
    out = capsys.readouterr().out
    assert out.startswith("Error:")
    assert "Traceback" not in out


def test_expire_sweep_transitions_stale_request_and_reports(monkeypatch, capsys):
    database = _make_database()
    _seed_order(
        database,
        "ORD-CLI-8",
        required_ship_date=_TODAY + timedelta(days=10),
        requested_delivery_date=_TODAY + timedelta(days=12),
        retailer_id="RET-CLI-8",
        extension_response_sla_hours=48,
    )
    request = _create_request(database, "ORD-CLI-8", "DELAY", _TODAY + timedelta(days=16))
    as_of = request["requested_at"] + timedelta(hours=49)

    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr(
        "sys.argv",
        ["run_po_delivery_change_cli.py", "expire-sweep", "--as-of", as_of.isoformat()],
    )

    assert _invoke_main() == 0
    out = capsys.readouterr().out
    assert "Expired 1 stale PO delivery-change request(s)." in out
    assert request["request_id"] in out
    assert "ORD-CLI-8" in out

    with database.session() as session:
        po_delivery_change_requests = PoDeliveryChangeRequestRepository(session)
        row = po_delivery_change_requests.get_by_request_id(request["request_id"])
    assert row["status"] == "EXPIRED"


def test_expire_sweep_no_stale_requests_reports_zero(monkeypatch, capsys):
    database = _make_database()
    monkeypatch.setattr(cli, "Database", lambda *_a, **_k: database)
    monkeypatch.setattr("sys.argv", ["run_po_delivery_change_cli.py", "expire-sweep"])

    assert _invoke_main() == 0
    out = capsys.readouterr().out
    assert "Expired 0 stale PO delivery-change request(s)." in out
