"""Tests for MitigationOptionRepository (`penalties.mitigation_option`).
Was tests/unit/repositories/test_mitigation_result_repository.py against
`MitigationResultRepository` -- relocated and renamed per the approved
plan's naming decision (see app/repositories/penalties/mitigation.py's
module docstring for the ORM-class/dataclass name collision this
inherits, not introduces)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.repositories.penalties.mitigation import MitigationOptionRepository
from app.services.penalties.mitigation.types import MitigationOption


def _option(action: str, net_saving: float) -> MitigationOption:
    return MitigationOption(
        action=action,
        projected_penalty_after=100.0 - net_saving,
        action_cost=0.0,
        net_saving=net_saving,
        risk_level="LOW",
        confidence="CONFIRMED",
        rationale=f"rationale for {action}",
    )


def _seed_purchase_order(repos, number: str) -> UUID:
    retailer = repos.master_data.add_retailer(f"RET-{number}", "Retailer", None, "SUM")
    purchase_order = repos.purchase_orders.create_purchase_order(
        purchase_order_number=number,
        retailer_id=retailer["id"],
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    return purchase_order["id"]


def test_save_results_persists_one_row_per_option(repos, db_session):
    purchase_order_id = _seed_purchase_order(repos, "ORD-REPO-1")
    repo = MitigationOptionRepository(db_session)

    options = [_option("ACCEPT", 0.0), _option("SPEED_UP_PRODUCTION", 40.0)]
    repo.save_results(purchase_order_id, date(2026, 8, 5), options)

    rows = repo.list_for_date(purchase_order_id, date(2026, 8, 5))
    assert {r["action"] for r in rows} == {"ACCEPT", "SPEED_UP_PRODUCTION"}
    # Ranked by net_saving, descending.
    assert rows[0]["action"] == "SPEED_UP_PRODUCTION"


def test_save_results_upserts_on_repeated_call_same_key(repos, db_session):
    purchase_order_id = _seed_purchase_order(repos, "ORD-REPO-2")
    repo = MitigationOptionRepository(db_session)

    repo.save_results(purchase_order_id, date(2026, 8, 5), [_option("ACCEPT", 0.0)])
    repo.save_results(purchase_order_id, date(2026, 8, 5), [_option("ACCEPT", 0.0)])

    rows = repo.list_for_date(purchase_order_id, date(2026, 8, 5))
    assert len(rows) == 1


def test_save_results_updates_fields_in_place_on_recompute(repos, db_session):
    purchase_order_id = _seed_purchase_order(repos, "ORD-REPO-3")
    repo = MitigationOptionRepository(db_session)

    repo.save_results(purchase_order_id, date(2026, 8, 5), [_option("ACCEPT", 0.0)])
    repo.save_results(purchase_order_id, date(2026, 8, 5), [_option("ACCEPT", 5.0)])

    rows = repo.list_for_date(purchase_order_id, date(2026, 8, 5))
    assert len(rows) == 1
    assert rows[0]["net_saving"] == 5.0


def test_get_latest_returns_the_most_recent_projection_date(repos, db_session):
    purchase_order_id = _seed_purchase_order(repos, "ORD-REPO-4")
    repo = MitigationOptionRepository(db_session)

    repo.save_results(purchase_order_id, date(2026, 8, 1), [_option("ACCEPT", 0.0)])
    repo.save_results(
        purchase_order_id, date(2026, 8, 5), [_option("ACCEPT", 0.0), _option("SPLIT_SHIPMENT", 10.0)]
    )

    rows = repo.get_latest(purchase_order_id)
    assert {r["action"] for r in rows} == {"ACCEPT", "SPLIT_SHIPMENT"}
    assert all(r["projection_date"] == date(2026, 8, 5) for r in rows)


def test_get_latest_returns_empty_list_when_nothing_persisted(repos, db_session):
    repo = MitigationOptionRepository(db_session)
    from uuid import uuid4

    assert repo.get_latest(uuid4()) == []


def test_get_latest_not_after_finds_nearest_prior_date(repos, db_session):
    purchase_order_id = _seed_purchase_order(repos, "ORD-REPO-5")
    repo = MitigationOptionRepository(db_session)

    repo.save_results(purchase_order_id, date(2026, 8, 1), [_option("ACCEPT", 0.0)])
    repo.save_results(purchase_order_id, date(2026, 8, 5), [_option("ACCEPT", 0.0)])

    rows = repo.get_latest_not_after(purchase_order_id, date(2026, 8, 3))
    assert len(rows) == 1
    assert rows[0]["projection_date"] == date(2026, 8, 1)


def test_get_latest_not_after_returns_empty_before_any_row(repos, db_session):
    purchase_order_id = _seed_purchase_order(repos, "ORD-REPO-6")
    repo = MitigationOptionRepository(db_session)
    repo.save_results(purchase_order_id, date(2026, 8, 5), [_option("ACCEPT", 0.0)])

    assert repo.get_latest_not_after(purchase_order_id, date(2026, 8, 1)) == []


def test_earliest_date_returns_none_when_nothing_persisted(repos, db_session):
    repo = MitigationOptionRepository(db_session)
    from uuid import uuid4

    assert repo.earliest_date(uuid4()) is None


def test_earliest_date_returns_the_minimum_projection_date(repos, db_session):
    purchase_order_id = _seed_purchase_order(repos, "ORD-REPO-7")
    repo = MitigationOptionRepository(db_session)
    repo.save_results(purchase_order_id, date(2026, 8, 5), [_option("ACCEPT", 0.0)])
    repo.save_results(purchase_order_id, date(2026, 8, 1), [_option("ACCEPT", 0.0)])

    assert repo.earliest_date(purchase_order_id) == date(2026, 8, 1)
