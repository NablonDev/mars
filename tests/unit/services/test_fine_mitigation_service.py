"""Tests for FineMitigationService -- the mitigation-options counterpart
to FineProjectionService, evaluating against an already-persisted
projection rather than recomputing one."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.exceptions import NoMitigationOptionsExistError, NoProjectionExistsError, OrderNotFoundError
from app.repositories.fine_mitigation.mitigation import MitigationResultRepository
from app.services.fine_mitigation.service import FineMitigationService
from app.services.fine_mitigation.types import ShortageCause


@pytest.fixture
def mitigation_result_repo(db_session) -> MitigationResultRepository:
    return MitigationResultRepository(db_session)


def _build_service(services, mitigation_result_repo) -> FineMitigationService:
    return FineMitigationService(
        orders=services.orders,
        rules=services.rules,
        master_data=services.master_data,
        projections=services.projections,
        mitigation_inputs=services.mitigation,
        mitigation_results=mitigation_result_repo,
    )


def _seed_shortage_order(services, order_id: str = "ORD-MIT") -> None:
    """A shortage-only order (300-unit gap out of 1000) with a per-unit
    rule, no delay rule -- SPEED_UP_PRODUCTION should be structurally
    eligible once cause/cost data is provided."""
    services.master_data.add_retailer("RET-MIT", "Retailer Mit", None, "SUM")
    services.master_data.add_sku("SKU-MIT", "MAT-MIT", None)
    services.master_data.add_location("LOC-MIT", None, None)
    services.orders.create_order(
        order_id=order_id,
        retailer_id="RET-MIT",
        sku_id="SKU-MIT",
        ship_from_location_id="LOC-MIT",
        order_qty=1000,
        unit_price=10.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    services.rules.add_rule(
        rule_id="RULE-MIT-SHORT",
        retailer_id="RET-MIT",
        violation_type="SHORT_SHIP",
        calc_type="PER_UNIT",
        rate=4.0,
        threshold_pct=0.0,
    )
    services.orders.add_confirmation(
        order_id=order_id,
        confirmation_id=f"CONF-{order_id}",
        confirmed_qty=700,
        confirmation_date=date(2026, 8, 5),
    )
    result = services.projection_service.run_for_order(order_id, date(2026, 8, 5))
    assert result.total_expected_fine > 0
    services.mitigation.upsert_inputs(
        order_id=order_id,
        shortage_cause=ShortageCause.LABOR_CAPACITY.value,
        shortage_cause_confirmed=True,
        capacity_boost_cost_per_unit=3.0,
        capacity_boost_max_units_per_day=200,
        capacity_boost_data_confirmed=True,
    )


def test_order_not_found_raises_order_not_found_error(services, mitigation_result_repo):
    service = _build_service(services, mitigation_result_repo)

    with pytest.raises(OrderNotFoundError, match="NOPE"):
        service.run_for_order("NOPE")


def test_no_projection_exists_raises(services, mitigation_result_repo):
    services.master_data.add_retailer("RET-EMPTY", "Retailer Empty", None, "SUM")
    services.master_data.add_sku("SKU-EMPTY", "MAT-EMPTY", None)
    services.master_data.add_location("LOC-EMPTY", None, None)
    services.orders.create_order(
        order_id="ORD-EMPTY",
        retailer_id="RET-EMPTY",
        sku_id="SKU-EMPTY",
        ship_from_location_id="LOC-EMPTY",
        order_qty=100,
        unit_price=5.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    service = _build_service(services, mitigation_result_repo)

    with pytest.raises(NoProjectionExistsError, match="ORD-EMPTY"):
        service.run_for_order("ORD-EMPTY", date(2026, 8, 5))


def test_run_for_order_computes_and_persists_ranked_options(services, mitigation_result_repo):
    _seed_shortage_order(services)
    service = _build_service(services, mitigation_result_repo)

    projection_date, options = service.run_for_order("ORD-MIT", date(2026, 8, 5))

    assert projection_date == date(2026, 8, 5)
    actions = [o.action for o in options]
    assert "ACCEPT" in actions
    assert "SPEED_UP_PRODUCTION" in actions
    # Ranked by net_saving, descending.
    assert [o.net_saving for o in options] == sorted((o.net_saving for o in options), reverse=True)

    persisted = mitigation_result_repo.list_for_date("ORD-MIT", date(2026, 8, 5))
    assert {row["action"] for row in persisted} == set(actions)


def test_run_for_order_defaults_projection_date_to_the_only_projected_day(services, mitigation_result_repo):
    _seed_shortage_order(services)
    service = _build_service(services, mitigation_result_repo)

    # today's UTC date almost certainly has no projection row for this
    # order -- exercising the "no projection for the resolved date" path
    # separately from the explicit-date happy path above.
    with pytest.raises(NoProjectionExistsError):
        service.run_for_order("ORD-MIT")


def test_run_for_order_is_idempotent_on_repeated_calls(services, mitigation_result_repo):
    _seed_shortage_order(services)
    service = _build_service(services, mitigation_result_repo)

    _, first = service.run_for_order("ORD-MIT", date(2026, 8, 5))
    _, second = service.run_for_order("ORD-MIT", date(2026, 8, 5))

    assert {o.action for o in first} == {o.action for o in second}
    rows = mitigation_result_repo.list_for_date("ORD-MIT", date(2026, 8, 5))
    # One row per action, not duplicated by the second run.
    assert len(rows) == len(first)


def test_get_latest_raises_when_nothing_has_been_computed_yet(services, mitigation_result_repo):
    _seed_shortage_order(services)
    service = _build_service(services, mitigation_result_repo)

    with pytest.raises(NoMitigationOptionsExistError, match="ORD-MIT"):
        service.get_latest("ORD-MIT")


def test_get_latest_returns_the_most_recently_computed_day(services, mitigation_result_repo):
    _seed_shortage_order(services)
    service = _build_service(services, mitigation_result_repo)
    service.run_for_order("ORD-MIT", date(2026, 8, 5))

    projection_date, rows = service.get_latest("ORD-MIT")

    assert projection_date == date(2026, 8, 5)
    assert rows
    assert rows == sorted(rows, key=lambda r: r["net_saving"], reverse=True)


def test_get_latest_raises_order_not_found_for_unknown_order(services, mitigation_result_repo):
    service = _build_service(services, mitigation_result_repo)

    with pytest.raises(OrderNotFoundError):
        service.get_latest("NOPE")


def test_current_stacking_mode_used_not_a_historical_override(services, mitigation_result_repo):
    """run_for_order rebuilds the ProjectionResult using the retailer's
    *current* stacking_mode, matching FineProjectionService's own default
    (non-override) behavior."""
    _seed_shortage_order(services)
    service = _build_service(services, mitigation_result_repo)

    _, options = service.run_for_order("ORD-MIT", date(2026, 8, 5))
    accept = next(o for o in options if o.action == "ACCEPT")

    history = services.projections.list_history("ORD-MIT")
    day_rows = [r for r in history if r["projection_date"] == date(2026, 8, 5)]
    expected_total = round(sum(r["projected_fine_amount"] for r in day_rows), 2)
    assert accept.projected_fine_after == expected_total


def test_reconstructed_projection_result_handles_max_stacking(services, mitigation_result_repo):
    services.master_data.add_retailer("RET-MAX", "Retailer Max", None, "MAX")
    services.master_data.add_sku("SKU-MAX", "MAT-MAX", None)
    services.master_data.add_location("LOC-MAX", None, None)
    services.orders.create_order(
        order_id="ORD-MAX",
        retailer_id="RET-MAX",
        sku_id="SKU-MAX",
        ship_from_location_id="LOC-MAX",
        order_qty=1000,
        unit_price=10.0,
        order_date=date(2026, 8, 1),
        requested_delivery_date=date(2026, 8, 10),
        required_ship_date=date(2026, 8, 8),
    )
    services.rules.add_rule(
        rule_id="RULE-MAX-SHORT",
        retailer_id="RET-MAX",
        violation_type="SHORT_SHIP",
        calc_type="PER_UNIT",
        rate=4.0,
    )
    services.rules.add_rule(
        rule_id="RULE-MAX-OTIF",
        retailer_id="RET-MAX",
        violation_type="OTIF_LATE",
        calc_type="FLAT_FEE",
        rate=500.0,
    )
    services.orders.add_confirmation(
        order_id="ORD-MAX",
        confirmation_id="CONF-ORD-MAX",
        confirmed_qty=700,
        confirmation_date=date(2026, 8, 5),
    )
    services.projection_service.run_for_order("ORD-MAX", date(2026, 8, 5))
    service = _build_service(services, mitigation_result_repo)

    _, options = service.run_for_order("ORD-MAX", date(2026, 8, 5))
    accept = next(o for o in options if o.action == "ACCEPT")

    history = services.projections.list_history("ORD-MAX")
    day_rows = [r for r in history if r["projection_date"] == date(2026, 8, 5)]
    expected_total = round(max(r["projected_fine_amount"] for r in day_rows), 2)
    assert accept.projected_fine_after == expected_total


def test_raw_material_shortage_cause_excludes_speed_up_production(services, mitigation_result_repo):
    _seed_shortage_order(services, "ORD-RAWMAT")
    services.mitigation.upsert_inputs(
        order_id="ORD-RAWMAT",
        shortage_cause=ShortageCause.RAW_MATERIAL.value,
        shortage_cause_confirmed=True,
        capacity_boost_cost_per_unit=3.0,
        capacity_boost_max_units_per_day=200,
        capacity_boost_data_confirmed=True,
    )
    service = _build_service(services, mitigation_result_repo)

    _, options = service.run_for_order("ORD-RAWMAT", date(2026, 8, 5))

    assert "SPEED_UP_PRODUCTION" not in {o.action for o in options}
