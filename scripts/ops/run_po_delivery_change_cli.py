"""
No-server CLI: exercises the PO delivery-date change request/response
lifecycle (`PoDeliveryChangeRequestService`) directly against the
configured database (DATABASE_URL / .env), without needing `uvicorn`
running. Kept for ad-hoc/local use -- the API is the equivalent for
anything that should go through HTTP:
    POST   /orders/{order_id}/po-delivery-change-requests   <-> create
    POST   /po-delivery-change-requests/{request_id}/response <-> respond
    GET    /orders/{order_id}/po-delivery-change-requests    <-> history

There is no HTTP equivalent for `expire-sweep`: it only ever runs inside
the nightly batch (`scripts/ops/run_daily_batch.py`, via
`app.workers.fine_projection.sweep_expired_po_delivery_change_requests`).
This subcommand exists purely to exercise/inspect that sweep ad hoc,
the same reason `run_mitigation_cli.py` depends on
`run_projection_cli.py` having been run first for the same date --
see that module's docstring.

Examples:
    python scripts/ops/run_po_delivery_change_cli.py create --order-id WMT-100234 --reason-code SHORTAGE --proposed-delivery-date 2026-08-20
    python scripts/ops/run_po_delivery_change_cli.py respond --request-id ext_xxxxxxxxxxxx --decision ACCEPTED
    python scripts/ops/run_po_delivery_change_cli.py respond --request-id ext_xxxxxxxxxxxx --decision COUNTERED --countered-delivery-date 2026-08-18
    python scripts/ops/run_po_delivery_change_cli.py history --order-id WMT-100234
    python scripts/ops/run_po_delivery_change_cli.py expire-sweep

`--now`/`--as-of` are optional ISO datetime overrides (e.g.
2026-08-05T00:00:00), for ad-hoc testing against the seeded mock
scenarios that live on a fictional past/future calendar -- same reason
the seeding module never uses wall-clock `now` (see
`app.services.fine_projection.po_delivery_change`'s module docstring).
Omitted, each defaults to real wall-clock time.
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.session import Database
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_projection.po_delivery_change_request import PoDeliveryChangeRequestRepository
from app.repositories.fine_projection.projection import ProjectionRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.order import OrderRepository
from app.services.fine_projection.po_delivery_change import PoDeliveryChangeRequestService
from app.services.fine_projection.service import FineProjectionService


def _build_service(session: Session) -> PoDeliveryChangeRequestService:
    """Same construction this service always uses -- see
    app.workers.fine_projection.sweep_expired_po_delivery_change_requests, the
    other non-HTTP caller."""
    master_data = MasterDataRepository(session)
    return PoDeliveryChangeRequestService(
        orders=OrderRepository(session),
        po_delivery_change_requests=PoDeliveryChangeRequestRepository(session),
        projection_service=FineProjectionService(
            orders=OrderRepository(session),
            rules=FineRuleRepository(session),
            master_data=master_data,
            projections=ProjectionRepository(session),
        ),
        master_data=master_data,
    )


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007


def _format_request_line(row: dict) -> str:
    parts = [
        f"{row['request_id']:<18} {row['reason_code']:<10} {row['status']:<10}",
        f"requested_at={row['requested_at']}",
        f"expires_at={row['expires_at']}",
        f"baseline={row['baseline_delivery_date']}",
        f"proposed={row['proposed_delivery_date']}",
    ]
    if row["countered_delivery_date"] is not None:
        parts.append(f"countered={row['countered_delivery_date']}")
    if row["resolved_at"] is not None:
        parts.append(f"resolved_at={row['resolved_at']}")
    return "  " + "  ".join(parts)


def _run_create(service: PoDeliveryChangeRequestService, args: argparse.Namespace) -> int:
    now = datetime.fromisoformat(args.now) if args.now else None
    row = service.create_request(
        args.order_id,
        args.reason_code,
        _parse_date(args.proposed_delivery_date),
        notes=args.notes,
        now=now,
    )
    print(f"Created {row['request_id']} for order {row['order_id']} (status={row['status']})")
    print(_format_request_line(row))
    return 0


def _run_respond(service: PoDeliveryChangeRequestService, args: argparse.Namespace) -> int:
    now = datetime.fromisoformat(args.now) if args.now else None
    countered_delivery_date = (
        _parse_date(args.countered_delivery_date) if args.countered_delivery_date else None
    )
    row = service.record_response(
        args.request_id,
        args.decision,
        countered_delivery_date=countered_delivery_date,
        now=now,
    )
    print(f"Recorded {args.decision} for {row['request_id']} (order {row['order_id']})")
    print(_format_request_line(row))
    return 0


def _run_history(service: PoDeliveryChangeRequestService, args: argparse.Namespace) -> int:
    # Mirrors GET /orders/{order_id}/po-delivery-change-requests, which
    # 404s via require_order before calling list_history -- list_history
    # itself does no existence check (an order with no requests and a
    # nonexistent order both return []).
    service.orders.require_order(args.order_id)
    rows = service.list_history(args.order_id)
    if not rows:
        print(f"No PO delivery-change requests found for order {args.order_id!r}.")
        return 0
    print(f"PO delivery-change request history for order {args.order_id!r} ({len(rows)}):")
    for row in rows:
        print(_format_request_line(row))
    return 0


def _run_expire_sweep(service: PoDeliveryChangeRequestService, args: argparse.Namespace) -> int:
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    expired = service.expire_stale(as_of)
    print(f"Expired {len(expired)} stale PO delivery-change request(s).")
    for row in expired:
        print(f"  {row['request_id']}  order_id={row['order_id']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a PO delivery-date change request")
    create_parser.add_argument("--order-id", required=True)
    create_parser.add_argument("--reason-code", required=True, choices=["SHORTAGE", "DELAY", "OTHER"])
    create_parser.add_argument("--proposed-delivery-date", required=True, help="YYYY-MM-DD")
    create_parser.add_argument("--notes")
    create_parser.add_argument("--now", help="ISO datetime override (default: wall-clock)")

    respond_parser = subparsers.add_parser("respond", help="Record a retailer's response to a request")
    respond_parser.add_argument("--request-id", required=True)
    respond_parser.add_argument("--decision", required=True, choices=["ACCEPTED", "COUNTERED", "REJECTED"])
    respond_parser.add_argument(
        "--countered-delivery-date", help="YYYY-MM-DD, required iff --decision COUNTERED"
    )
    respond_parser.add_argument("--now", help="ISO datetime override (default: wall-clock)")

    history_parser = subparsers.add_parser(
        "history", help="List an order's PO delivery-change request history"
    )
    history_parser.add_argument("--order-id", required=True)

    expire_parser = subparsers.add_parser(
        "expire-sweep",
        help="Expire PENDING requests past their SLA -- no HTTP equivalent, see module docstring",
    )
    expire_parser.add_argument("--as-of", help="ISO datetime override (default: wall-clock)")

    args = parser.parse_args()

    settings = get_settings()
    database = Database(settings.database_url)
    with database.session() as session:
        service = _build_service(session)
        try:
            if args.command == "create":
                return _run_create(service, args)
            if args.command == "respond":
                return _run_respond(service, args)
            if args.command == "history":
                return _run_history(service, args)
            return _run_expire_sweep(service, args)
        except AppError as exc:
            print(f"Error: {exc}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
