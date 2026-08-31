"""Unit tests for FineMitigationSummaryService's content fingerprint -- a
pure, DB-free function over the ranked mitigation options themselves
(not the projection's own fingerprint fields), deliberately excluding
current_projection_date. See app/services/fine_mitigation/summary.py's
_compute_content_fingerprint docstring for why."""

from datetime import date
from typing import Any

from app.agents.penalties.mitigation import (
    MitigationOptionContext,
    OrderContext,
    PenaltyMitigationSummaryContext,
)
from app.services.penalties.mitigation.summary_service import _compute_content_fingerprint, _fmt_number


def _order(**overrides: Any) -> OrderContext:
    fields: dict[str, Any] = {
        "order_id": "ORD-FP",
        "order_status": "OPEN",
        "retailer_name": "Retailer FP",
        "sku_description": "Widget",
        "order_qty": 1000,
        "unit_price": 10.0,
        "required_ship_date": date(2026, 8, 8),
        "requested_delivery_date": date(2026, 8, 10),
    }
    fields.update(overrides)
    return OrderContext(**fields)


def _option(**overrides: Any) -> MitigationOptionContext:
    fields: dict[str, Any] = {
        "action": "ACCEPT",
        "projected_penalty_after": 100.0,
        "action_cost": 0.0,
        "net_saving": 0.0,
        "risk_level": "HIGH",
        "confidence": "CONFIRMED",
        "rationale": "Pay the projected penalty as-is.",
    }
    fields.update(overrides)
    return MitigationOptionContext(**fields)


def _context(
    *,
    current_projection_date: date = date(2026, 8, 5),
    mitigation_options: list[MitigationOptionContext] | None = None,
    order: OrderContext | None = None,
    current_total_expected_penalty: float = 100.0,
    stacking_mode: str = "SUM",
) -> PenaltyMitigationSummaryContext:
    return PenaltyMitigationSummaryContext(
        order=order or _order(),
        current_projection_date=current_projection_date,
        current_total_expected_penalty=current_total_expected_penalty,
        stacking_mode=stacking_mode,
        mitigation_options=mitigation_options if mitigation_options is not None else [_option()],
    )


def test_identical_contexts_hash_the_same():
    assert _compute_content_fingerprint(_context()) == _compute_content_fingerprint(_context())


def test_changed_as_of_date_alone_hashes_identically():
    """The key property: current_projection_date is not part of the
    payload, so a request for a different as_of_date over the exact same
    options must not change the fingerprint."""
    fp_day1 = _compute_content_fingerprint(_context(current_projection_date=date(2026, 8, 5)))
    fp_day2 = _compute_content_fingerprint(_context(current_projection_date=date(2026, 8, 9)))

    assert fp_day1 == fp_day2


def test_changed_net_saving_changes_the_hash():
    baseline = _compute_content_fingerprint(_context())
    changed = _compute_content_fingerprint(_context(mitigation_options=[_option(net_saving=50.0)]))

    assert baseline != changed


def test_changed_action_cost_changes_the_hash():
    baseline = _compute_content_fingerprint(_context())
    changed = _compute_content_fingerprint(_context(mitigation_options=[_option(action_cost=30.0)]))

    assert baseline != changed


def test_added_option_changes_the_hash():
    baseline = _compute_content_fingerprint(_context())
    changed = _compute_content_fingerprint(
        _context(
            mitigation_options=[
                _option(),
                _option(
                    action="SPEED_UP_PRODUCTION",
                    net_saving=50.0,
                    action_cost=30.0,
                    projected_penalty_after=20.0,
                ),
            ]
        )
    )

    assert baseline != changed


def test_option_order_does_not_change_the_hash():
    """The fingerprint sorts options before hashing -- the engine's own
    ranking (by net_saving) is a display concern, not a content-identity
    concern."""
    a = _option(action="ACCEPT")
    b = _option(action="SPEED_UP_PRODUCTION", net_saving=50.0, action_cost=30.0, projected_penalty_after=20.0)

    fp1 = _compute_content_fingerprint(_context(mitigation_options=[a, b]))
    fp2 = _compute_content_fingerprint(_context(mitigation_options=[b, a]))

    assert fp1 == fp2


def test_changed_risk_level_changes_the_hash():
    baseline = _compute_content_fingerprint(_context())
    changed = _compute_content_fingerprint(_context(mitigation_options=[_option(risk_level="LOW")]))

    assert baseline != changed


def test_changed_confidence_changes_the_hash():
    baseline = _compute_content_fingerprint(_context())
    changed = _compute_content_fingerprint(_context(mitigation_options=[_option(confidence="ESTIMATED")]))

    assert baseline != changed


def test_changed_stacking_mode_changes_the_hash():
    baseline = _compute_content_fingerprint(_context())
    changed = _compute_content_fingerprint(_context(stacking_mode="MAX"))

    assert baseline != changed


def test_changed_order_status_changes_the_hash():
    baseline = _compute_content_fingerprint(_context())
    changed = _compute_content_fingerprint(_context(order=_order(order_status="DELIVERED")))

    assert baseline != changed


def test_changed_current_total_expected_penalty_changes_the_hash():
    baseline = _compute_content_fingerprint(_context())
    changed = _compute_content_fingerprint(_context(current_total_expected_penalty=250.0))

    assert baseline != changed


def test_empty_mitigation_options_does_not_crash():
    fingerprint = _compute_content_fingerprint(_context(mitigation_options=[]))

    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64


def test_fmt_number_is_stable_across_int_vs_float_and_float_rounding_noise():
    assert _fmt_number(10) == _fmt_number(10.0) == _fmt_number(10.00)
    assert _fmt_number(2.5000000000000004) == _fmt_number(2.5)
