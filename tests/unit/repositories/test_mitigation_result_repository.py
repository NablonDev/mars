"""Tests for MitigationResultRepository (mitigation_option)."""

from __future__ import annotations

from datetime import date

from app.repositories.fine_mitigation.mitigation import MitigationResultRepository
from app.services.fine_mitigation.types import MitigationOption


def _option(action: str, net_saving: float) -> MitigationOption:
    return MitigationOption(
        action=action,
        projected_fine_after=100.0 - net_saving,
        action_cost=0.0,
        net_saving=net_saving,
        risk_level="LOW",
        confidence="CONFIRMED",
        rationale=f"rationale for {action}",
    )


def _seed_order(services, order_id: str) -> None:
    services.master_data.add_retailer(f"RET-{order_id}", "Retailer", None, "SUM")
    services.master_data.add_sku(f"SKU-{order_id}", "MAT", None)
    services.master_data.add_location(f"LOC-{order_id}", None, None)
    services.orders.create_order(
        order_id=order_id,
        retailer_id=f"RET-{order_id}",
        sku_id=f"SKU-{order_id}",
        ship_from_location_id=f"LOC-{order_id}",
        order_qty=100,
        unit_price=5.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )


def test_save_results_persists_one_row_per_option(services, db_session):
    _seed_order(services, "ORD-REPO-1")
    repo = MitigationResultRepository(db_session)

    options = [_option("ACCEPT", 0.0), _option("SPEED_UP_PRODUCTION", 40.0)]
    repo.save_results("ORD-REPO-1", date(2026, 8, 5), options)

    rows = repo.list_for_date("ORD-REPO-1", date(2026, 8, 5))
    assert {r["action"] for r in rows} == {"ACCEPT", "SPEED_UP_PRODUCTION"}
    # Ranked by net_saving, descending.
    assert rows[0]["action"] == "SPEED_UP_PRODUCTION"


def test_save_results_upserts_on_repeated_call_same_key(services, db_session):
    _seed_order(services, "ORD-REPO-2")
    repo = MitigationResultRepository(db_session)

    repo.save_results("ORD-REPO-2", date(2026, 8, 5), [_option("ACCEPT", 0.0)])
    repo.save_results("ORD-REPO-2", date(2026, 8, 5), [_option("ACCEPT", 0.0)])

    rows = repo.list_for_date("ORD-REPO-2", date(2026, 8, 5))
    assert len(rows) == 1


def test_save_results_updates_fields_in_place_on_recompute(services, db_session):
    _seed_order(services, "ORD-REPO-3")
    repo = MitigationResultRepository(db_session)

    repo.save_results("ORD-REPO-3", date(2026, 8, 5), [_option("ACCEPT", 0.0)])
    repo.save_results("ORD-REPO-3", date(2026, 8, 5), [_option("ACCEPT", 5.0)])

    rows = repo.list_for_date("ORD-REPO-3", date(2026, 8, 5))
    assert len(rows) == 1
    assert rows[0]["net_saving"] == 5.0


def test_get_latest_returns_the_most_recent_projection_date(services, db_session):
    _seed_order(services, "ORD-REPO-4")
    repo = MitigationResultRepository(db_session)

    repo.save_results("ORD-REPO-4", date(2026, 8, 1), [_option("ACCEPT", 0.0)])
    repo.save_results(
        "ORD-REPO-4", date(2026, 8, 5), [_option("ACCEPT", 0.0), _option("SPLIT_SHIPMENT", 10.0)]
    )

    rows = repo.get_latest("ORD-REPO-4")
    assert {r["action"] for r in rows} == {"ACCEPT", "SPLIT_SHIPMENT"}
    assert all(r["projection_date"] == date(2026, 8, 5) for r in rows)


def test_get_latest_returns_empty_list_when_nothing_persisted(services, db_session):
    repo = MitigationResultRepository(db_session)
    assert repo.get_latest("ORD-NEVER-RUN") == []


def test_get_latest_not_after_finds_nearest_prior_date(services, db_session):
    _seed_order(services, "ORD-REPO-5")
    repo = MitigationResultRepository(db_session)

    repo.save_results("ORD-REPO-5", date(2026, 8, 1), [_option("ACCEPT", 0.0)])
    repo.save_results("ORD-REPO-5", date(2026, 8, 5), [_option("ACCEPT", 0.0)])

    rows = repo.get_latest_not_after("ORD-REPO-5", date(2026, 8, 3))
    assert len(rows) == 1
    assert rows[0]["projection_date"] == date(2026, 8, 1)


def test_get_latest_not_after_returns_empty_before_any_row(services, db_session):
    _seed_order(services, "ORD-REPO-6")
    repo = MitigationResultRepository(db_session)
    repo.save_results("ORD-REPO-6", date(2026, 8, 5), [_option("ACCEPT", 0.0)])

    assert repo.get_latest_not_after("ORD-REPO-6", date(2026, 8, 1)) == []


def test_earliest_date_returns_none_when_nothing_persisted(services, db_session):
    repo = MitigationResultRepository(db_session)
    assert repo.earliest_date("ORD-NEVER-RUN") is None


def test_earliest_date_returns_the_minimum_projection_date(services, db_session):
    _seed_order(services, "ORD-REPO-7")
    repo = MitigationResultRepository(db_session)
    repo.save_results("ORD-REPO-7", date(2026, 8, 5), [_option("ACCEPT", 0.0)])
    repo.save_results("ORD-REPO-7", date(2026, 8, 1), [_option("ACCEPT", 0.0)])

    assert repo.earliest_date("ORD-REPO-7") == date(2026, 8, 1)
