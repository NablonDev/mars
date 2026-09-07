"""Tests for `DisputeService`: state transitions, all three `analyze()`
error paths, and one full-lifecycle test (open -> analyze -> resolve with
override -> assert final state and audit fields) against the SQLite test
DB -- same "unit test doubling as the full-flow/integration check" posture
`tests/unit/services/test_po_delivery_change_request_service.py` already
uses for its own sibling lifecycle record (no live Postgres needed; see
`tests/conftest.py`'s module docstring).

Real, post-delivery facts are seeded via `delivery`/`delivery_line`
(shortage) and `shipment` (delay) -- never `order_confirmation`, the
pre-delivery promise a dispute must not use (see
`app.services.penalties.dispute.types`'s module docstring).
"""

from datetime import date, datetime

import pytest

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.services.penalties.dispute.service import DisputeService
from app.services.penalties.projection.service import ProjectionService

_ORDER_QTY = 100
_UNIT_PRICE = 10.0
_REQUESTED_DELIVERY_DATE = date(2026, 6, 10)


def _build_service(repos) -> DisputeService:
    projection_service = ProjectionService(
        purchase_orders=repos.purchase_orders,
        fulfillment=repos.fulfillment,
        rules=repos.penalty_rules,
        master_data=repos.master_data,
        projections=repos.penalty_projections,
    )
    return DisputeService(
        purchase_orders=repos.purchase_orders,
        disputes=repos.disputes,
        actual_penalties=repos.actual_penalties,
        rules=repos.penalty_rules,
        projection_service=projection_service,
    )


def _seed_order(
    repos,
    po_number: str,
    *,
    violation_type: str = "SHORT_SHIP",
    calc_type: str = "PER_UNIT",
    rate: float = 5.0,
    grace_period_days: int = 0,
    rule_effective_start: date = date(2026, 1, 1),
    rule_effective_end: date | None = None,
):
    """Seeds a PO with one penalty rule, no fulfillment facts of its own --
    callers attach delivery/delivery_line and/or shipment rows themselves.
    Returns (purchase_order_id, line_id, retailer_id)."""
    retailer = repos.master_data.add_retailer(f"RET-{po_number}", "Dispute Test Retailer", None, "SUM")
    material = repos.master_data.add_material(f"MAT-{po_number}", None)
    plant = repos.master_data.add_plant(f"PLANT-{po_number}", None, None)
    repos.penalty_rules.add_rule(
        rule_code=f"RULE-{po_number}",
        retailer_id=retailer["id"],
        violation_type=violation_type,
        calc_type=calc_type,
        rate=rate,
        grace_period_days=grace_period_days,
        effective_start_date=rule_effective_start,
        effective_end_date=rule_effective_end,
    )

    purchase_order = repos.purchase_orders.create_purchase_order(
        purchase_order_number=po_number,
        retailer_id=retailer["id"],
        order_date=date(2026, 5, 1),
        requested_delivery_date=_REQUESTED_DELIVERY_DATE,
        required_ship_date=date(2026, 6, 8),
        order_status="DELIVERED",
    )
    line = repos.purchase_orders.add_line(
        purchase_order_id=purchase_order["id"],
        line_number="10",
        ordered_quantity=_ORDER_QTY,
        unit_price=_UNIT_PRICE,
        material_id=material["id"],
        plant_id=plant["id"],
    )
    return purchase_order["id"], line["id"], retailer["id"]


def _seed_delivery(repos, purchase_order_id, line_id, delivered_qty: float, as_of_date: date):
    delivery = repos.fulfillment.add_delivery(
        delivery_number=f"DELIV-{purchase_order_id}",
        purchase_order_id=purchase_order_id,
        actual_delivery_date=as_of_date,
    )
    repos.fulfillment.add_delivery_line(
        delivery_id=delivery["id"],
        purchase_order_line_id=line_id,
        delivered_quantity=delivered_qty,
    )
    return delivery


def _seed_shipment(repos, purchase_order_id, actual_delivery_date: date, recorded_at: date):
    delivery_rows = repos.fulfillment.list_deliveries_for_purchase_order(purchase_order_id)
    if delivery_rows:
        delivery = delivery_rows[0]
    else:
        delivery = repos.fulfillment.add_delivery(
            delivery_number=f"DELIV-{purchase_order_id}", purchase_order_id=purchase_order_id
        )
    repos.fulfillment.add_shipment(
        shipment_number=f"SHIP-{purchase_order_id}",
        delivery_id=delivery["id"],
        recorded_at=datetime.combine(recorded_at, datetime.min.time()),
        actual_delivery_date=actual_delivery_date,
        expected_delivery_date=_REQUESTED_DELIVERY_DATE,
    )


def _seed_shortage_dispute_scenario(
    repos,
    po_number: str = "ORD-DSP",
    *,
    delivered_qty: float = 90.0,
    charge_date: date = date(2026, 6, 12),
    claimed_amount: float = 80.0,
    rate: float = 5.0,
    actual_penalty_amount: float | None = None,
):
    """Real, final shortfall = order_qty - delivered_qty = 10 units; at
    rate=5.0/unit that computes to $50 -- callers vary `claimed_amount` to
    land in whichever verdict branch they're testing. `actual_penalty_amount`
    defaults to `claimed_amount` (existing callers rely on the two being
    identical); pass it explicitly to seed an `actual_penalty` charge that
    differs from the dispute's own `claimed_amount`. Returns
    (purchase_order_id, actual_penalty_id)."""
    purchase_order_id, line_id, _retailer_id = _seed_order(
        repos, po_number, violation_type="SHORT_SHIP", rate=rate
    )
    _seed_delivery(repos, purchase_order_id, line_id, delivered_qty, charge_date)

    actual_penalty = repos.actual_penalties.add_actual_penalty(
        actual_penalty_number=f"AP-{po_number}",
        purchase_order_id=purchase_order_id,
        violation_type="SHORT_SHIP",
        actual_penalty_amount=(
            actual_penalty_amount if actual_penalty_amount is not None else claimed_amount
        ),
        invoice_or_deduction_date=charge_date,
    )
    return purchase_order_id, actual_penalty["id"]


# ---------------------------------------------------------------------------
# open_dispute
# ---------------------------------------------------------------------------


def test_open_dispute_success(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos)
    service = _build_service(repos)

    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0, notes="looks high")

    assert dispute["dispute_status"] == "OPEN"
    assert dispute["reason_code"] == "AMOUNT_INCORRECT"
    assert dispute["claimed_amount"] == 80.0
    assert dispute["verdict"] is None
    assert dispute["dispute_number"].startswith("DSP-")


def test_open_dispute_unknown_actual_penalty_raises_not_found(repos):
    from uuid import uuid4

    service = _build_service(repos)
    with pytest.raises(NotFoundError):
        service.open_dispute(uuid4(), "OTHER", 10.0)


def test_open_dispute_conflicts_with_existing_active_dispute(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos)
    service = _build_service(repos)
    service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)

    with pytest.raises(ConflictError):
        service.open_dispute(actual_penalty_id, "OTHER", 80.0)


def test_open_dispute_allowed_again_once_prior_is_terminal(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos)
    service = _build_service(repos)
    first = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)
    service.analyze(first["id"])
    service.resolve(first["id"], resolved_by="ops@mars.test")

    second = service.open_dispute(actual_penalty_id, "OTHER", 90.0)
    assert second["id"] != first["id"]
    assert second["dispute_status"] == "OPEN"


# ---------------------------------------------------------------------------
# analyze -- shortage
# ---------------------------------------------------------------------------


def test_analyze_pay_partial_when_retailer_overcharged(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos, claimed_amount=80.0)
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)

    analyzed = service.analyze(dispute["id"])

    assert analyzed["dispute_status"] == "ANALYZED"
    assert analyzed["computed_amount"] == 50.0  # 10 shortfall units x $5/unit
    assert analyzed["delta_amount"] == 30.0
    assert analyzed["verdict"] == "PAY_PARTIAL"
    assert analyzed["rule_id"] is not None
    assert analyzed["analyzed_at"] is not None
    assert analyzed["analysis_breakdown"]["violation_family"] == "SHORTAGE"
    assert analyzed["analysis_breakdown"]["facts"]["delivered_qty"] == 90.0
    assert analyzed["analysis_breakdown"]["facts"]["shortfall_units"] == 10.0


def test_analyze_uses_dispute_claimed_amount_not_actual_penalty_amount(repos):
    """Regression for the bug where `analyze()` recomputed against
    `actual_penalty["actual_penalty_amount"]` instead of the dispute's own
    `claimed_amount` -- `open_dispute()` lets the two diverge (the
    disputer's recorded claim need not match the original charge), and the
    verdict must be adjudicated against what the disputer actually claimed.

    `actual_penalty_amount=20.0` would round-trip to PAY_FULL (delta -30)
    if the bug regressed; the dispute's real `claimed_amount=80.0` must
    instead produce PAY_PARTIAL (delta +30) against the $50 computed
    shortfall charge."""
    _, actual_penalty_id = _seed_shortage_dispute_scenario(
        repos, claimed_amount=80.0, actual_penalty_amount=20.0
    )
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)

    analyzed = service.analyze(dispute["id"])

    assert analyzed["computed_amount"] == 50.0  # 10 shortfall units x $5/unit
    assert analyzed["delta_amount"] == 30.0
    assert analyzed["verdict"] == "PAY_PARTIAL"
    assert analyzed["analysis_breakdown"]["claimed_amount"] == 80.0


def test_analyze_no_pay_when_no_real_shortfall(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos, delivered_qty=100.0, claimed_amount=200.0)
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "QTY_CONFIRMED", 200.0)

    analyzed = service.analyze(dispute["id"])

    assert analyzed["verdict"] == "NO_PAY"
    assert analyzed["computed_amount"] == 0.0


def test_analyze_pay_full_undercharge_records_negative_delta(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos, delivered_qty=90.0, claimed_amount=20.0)
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 20.0)

    analyzed = service.analyze(dispute["id"])

    assert analyzed["verdict"] == "PAY_FULL"
    assert analyzed["computed_amount"] == 50.0
    assert analyzed["delta_amount"] == -30.0


def test_analyze_raises_no_matching_rule_when_no_rule_effective_on_charge_date(repos):
    _purchase_order_id, actual_penalty_id = _seed_shortage_dispute_scenario(
        repos, charge_date=date(2025, 1, 1)
    )
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)

    with pytest.raises(BusinessRuleError) as exc_info:
        service.analyze(dispute["id"])
    assert exc_info.value.code == "NO_MATCHING_RULE_FOR_DISPUTE"

    # No partial write -- the dispute is left completely untouched, still OPEN.
    unchanged = service.get(dispute["id"])
    assert unchanged["dispute_status"] == "OPEN"
    assert unchanged["computed_amount"] is None


def test_analyze_raises_insufficient_data_when_no_delivery_recorded(repos):
    purchase_order_id, _line_id, _retailer_id = _seed_order(
        repos, "ORD-DSP-NODATA", violation_type="SHORT_SHIP"
    )
    actual_penalty = repos.actual_penalties.add_actual_penalty(
        actual_penalty_number="AP-ORD-DSP-NODATA",
        purchase_order_id=purchase_order_id,
        violation_type="SHORT_SHIP",
        actual_penalty_amount=50.0,
        invoice_or_deduction_date=date(2026, 6, 12),
    )
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty["id"], "AMOUNT_INCORRECT", 50.0)

    with pytest.raises(BusinessRuleError) as exc_info:
        service.analyze(dispute["id"])
    assert exc_info.value.code == "INSUFFICIENT_DATA_FOR_DISPUTE"

    unchanged = service.get(dispute["id"])
    assert unchanged["dispute_status"] == "OPEN"


def test_analyze_refuses_once_terminal(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos)
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)
    service.analyze(dispute["id"])
    service.resolve(dispute["id"], resolved_by="ops@mars.test")

    with pytest.raises(ValidationError):
        service.analyze(dispute["id"])


# ---------------------------------------------------------------------------
# analyze -- delay + grace period + TIERED-delay unsupported
# ---------------------------------------------------------------------------


def test_analyze_delay_pay_full_undercharge(repos):
    purchase_order_id, _line_id, _retailer_id = _seed_order(
        repos, "ORD-DSP-DELAY", violation_type="OTIF_LATE", calc_type="PER_UNIT", rate=3.0
    )
    _seed_shipment(
        repos, purchase_order_id, actual_delivery_date=date(2026, 6, 15), recorded_at=date(2026, 6, 15)
    )
    actual_penalty = repos.actual_penalties.add_actual_penalty(
        actual_penalty_number="AP-ORD-DSP-DELAY",
        purchase_order_id=purchase_order_id,
        violation_type="OTIF_LATE",
        actual_penalty_amount=200.0,
        invoice_or_deduction_date=date(2026, 6, 20),
    )
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty["id"], "AMOUNT_INCORRECT", 200.0)

    analyzed = service.analyze(dispute["id"])

    assert analyzed["verdict"] == "PAY_FULL"
    assert analyzed["computed_amount"] == 300.0  # 100 units x $3/unit, no grace
    assert analyzed["delta_amount"] == -100.0
    assert analyzed["analysis_breakdown"]["violation_family"] == "DELAY"
    assert analyzed["analysis_breakdown"]["facts"]["is_late"] is True


def test_analyze_delay_no_pay_when_within_grace_period(repos):
    purchase_order_id, _line_id, _retailer_id = _seed_order(
        repos,
        "ORD-DSP-GRACE",
        violation_type="OTIF_LATE",
        calc_type="FLAT_FEE",
        rate=750.0,
        grace_period_days=3,
    )
    # 2 days late, within the 3-day grace period.
    _seed_shipment(
        repos, purchase_order_id, actual_delivery_date=date(2026, 6, 12), recorded_at=date(2026, 6, 12)
    )
    actual_penalty = repos.actual_penalties.add_actual_penalty(
        actual_penalty_number="AP-ORD-DSP-GRACE",
        purchase_order_id=purchase_order_id,
        violation_type="OTIF_LATE",
        actual_penalty_amount=750.0,
        invoice_or_deduction_date=date(2026, 6, 15),
    )
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty["id"], "NOT_LATE", 750.0)

    analyzed = service.analyze(dispute["id"])

    assert analyzed["verdict"] == "NO_PAY"
    assert analyzed["computed_amount"] == 0.0
    assert analyzed["analysis_breakdown"]["facts"]["is_late"] is False


def test_analyze_raises_dispute_calc_not_supported_for_tiered_delay_rule(repos):
    """A TIERED delay rule is a real, documented gap
    (`price_delay_penalty` has no tiered branch) -- `analyze()` must turn
    it into a clear `BusinessRuleError`, never a raw `NotImplementedError`."""
    retailer = repos.master_data.add_retailer("RET-DSP-TIERDELAY", "Dispute Test Retailer", None, "SUM")
    material = repos.master_data.add_material("MAT-DSP-TIERDELAY", None)
    plant = repos.master_data.add_plant("PLANT-DSP-TIERDELAY", None, None)
    repos.penalty_rules.add_rule(
        rule_code="RULE-DSP-TIERDELAY",
        retailer_id=retailer["id"],
        violation_type="OTIF_LATE",
        calc_type="TIERED",
        rate=0.0,
        effective_start_date=date(2026, 1, 1),
        tiers=[{"band_min": 0.0, "band_max": 1.0, "rate": 0.05}],
    )
    purchase_order = repos.purchase_orders.create_purchase_order(
        purchase_order_number="ORD-DSP-TIERDELAY",
        retailer_id=retailer["id"],
        order_date=date(2026, 5, 1),
        requested_delivery_date=_REQUESTED_DELIVERY_DATE,
        required_ship_date=date(2026, 6, 8),
        order_status="DELIVERED",
    )
    purchase_order_id = purchase_order["id"]
    repos.purchase_orders.add_line(
        purchase_order_id=purchase_order_id,
        line_number="10",
        ordered_quantity=_ORDER_QTY,
        unit_price=_UNIT_PRICE,
        material_id=material["id"],
        plant_id=plant["id"],
    )
    _seed_shipment(
        repos, purchase_order_id, actual_delivery_date=date(2026, 6, 20), recorded_at=date(2026, 6, 20)
    )
    actual_penalty = repos.actual_penalties.add_actual_penalty(
        actual_penalty_number="AP-ORD-DSP-TIERDELAY",
        purchase_order_id=purchase_order_id,
        violation_type="OTIF_LATE",
        actual_penalty_amount=100.0,
        invoice_or_deduction_date=date(2026, 6, 25),
    )
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty["id"], "AMOUNT_INCORRECT", 100.0)

    with pytest.raises(BusinessRuleError) as exc_info:
        service.analyze(dispute["id"])
    assert exc_info.value.code == "DISPUTE_CALC_NOT_SUPPORTED"

    unchanged = service.get(dispute["id"])
    assert unchanged["dispute_status"] == "OPEN"


# ---------------------------------------------------------------------------
# resolve / override
# ---------------------------------------------------------------------------


def test_resolve_requires_analyzed_status(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos)
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)

    with pytest.raises(ValidationError):
        service.resolve(dispute["id"], resolved_by="ops@mars.test")


def test_resolve_accepts_engine_verdict(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos)
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)
    service.analyze(dispute["id"])

    resolved = service.resolve(dispute["id"], resolved_by="ops@mars.test")

    assert resolved["dispute_status"] == "RESOLVED"
    assert resolved["resolved_by"] == "ops@mars.test"
    assert resolved["resolved_at"] is not None
    assert resolved["override_verdict"] is None


def test_resolve_override_requires_reason(repos):
    _, actual_penalty_id = _seed_shortage_dispute_scenario(repos)
    service = _build_service(repos)
    dispute = service.open_dispute(actual_penalty_id, "AMOUNT_INCORRECT", 80.0)
    service.analyze(dispute["id"])

    with pytest.raises(ValidationError):
        service.resolve(dispute["id"], resolved_by="ops@mars.test", override_verdict="PAY_FULL")


def test_full_lifecycle_open_analyze_resolve_with_override(repos):
    """Open -> analyze -> resolve with override -> assert final state and
    audit fields (the required end-to-end flow)."""
    purchase_order_id, actual_penalty_id = _seed_shortage_dispute_scenario(repos, claimed_amount=80.0)
    service = _build_service(repos)

    dispute = service.open_dispute(
        actual_penalty_id, "AMOUNT_INCORRECT", 80.0, notes="retailer overbilled us"
    )
    assert dispute["dispute_status"] == "OPEN"

    analyzed = service.analyze(dispute["id"])
    assert analyzed["dispute_status"] == "ANALYZED"
    assert analyzed["verdict"] == "PAY_PARTIAL"
    assert analyzed["computed_amount"] == 50.0
    assert analyzed["delta_amount"] == 30.0

    overridden = service.resolve(
        dispute["id"],
        resolved_by="ops-lead@mars.test",
        override_verdict="PAY_FULL",
        override_reason="Ops discretion: retailer relationship priority outweighs the $30 dispute.",
    )

    assert overridden["dispute_status"] == "OVERRIDDEN"
    assert overridden["override_verdict"] == "PAY_FULL"
    assert overridden["override_reason"]
    assert overridden["resolved_by"] == "ops-lead@mars.test"
    assert overridden["resolved_at"] is not None
    # The deterministic engine's own verdict/amounts are preserved
    # unchanged as the audit trail -- override records a human decision on
    # top of it, it does not erase it.
    assert overridden["verdict"] == "PAY_PARTIAL"
    assert overridden["computed_amount"] == 50.0

    final = service.get(dispute["id"])
    assert final["dispute_status"] == "OVERRIDDEN"
    assert final["purchase_order_id"] == purchase_order_id

    listed = service.list_for_purchase_order(purchase_order_id)
    assert [d["id"] for d in listed] == [dispute["id"]]
