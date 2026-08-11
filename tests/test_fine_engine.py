"""
Regression and coverage tests for fine_engine.py, written directly
in response to a code review that found:
  - no automated tests existed at all
  - cap_amount was never actually exercised (every capped rule in the
    mock data sat far below its cap)
  - SHORTAGE_LOCKED_IN_PROBABILITY was only exercised via a PERCENT_OF_PO
    rule, never a PER_UNIT rule
  - stacking_mode="MAX" was coded but never invoked anywhere
  - demand_exception_points() looked inert in the mock data, but that
    was a timing artifact, not a mechanism failure
  - the exact-10%-gap boundary bug that was found and fixed earlier by
    hand -- this suite exists specifically so the next one like it gets
    caught automatically instead.

Run with: pytest tests/test_fine_engine.py -v
"""

from datetime import date, timedelta

import pytest

from app.engine import (
    SHORTAGE_LOCKED_IN_PROBABILITY,
    AppointmentStatus,
    CalcType,
    FineRule,
    FineTier,
    OrderSnapshot,
    ProductionStatus,
    compute_delay_probability,
    compute_shortage_probability,
    price_delay_fine,
    price_shortage_fine,
    project_order,
    resolve_expected_ship_date,
)
from app.engine.shortage import _days_points, _demand_exception_points, _gap_points, _score_to_probability

TODAY = date(2026, 8, 1)


def make_snapshot(**overrides) -> OrderSnapshot:
    """A baseline, clean snapshot -- override only what a given test cares about."""
    defaults = {
        "order_id": "TEST-ORDER",
        "projection_date": TODAY,
        "order_qty": 1000,
        "unit_price": 10.0,
        "requested_delivery_date": TODAY + timedelta(days=10),
        "required_ship_date": TODAY + timedelta(days=8),
        "confirmed_qty": 1000,
        "production_status": ProductionStatus.ON_TRACK,
        "carrier_reliability_score": 95.0,
        "expected_transit_days": 2,
    }
    defaults.update(overrides)
    return OrderSnapshot(**defaults)


# ---------------------------------------------------------------------------
# Boundary regression tests -- these are exactly the kind of thing that
# broke silently once already (exactly-10% gap landing in the wrong bracket).
# ---------------------------------------------------------------------------


class TestGapPointsBoundaries:
    def test_zero_gap(self):
        assert _gap_points(0.0) == 0

    def test_just_under_10_pct(self):
        assert _gap_points(0.0999) == 10

    def test_exactly_10_pct_lands_in_upper_bracket(self):
        # Regression test for the bug found and fixed manually: 10% must
        # be treated as the start of the 10-30% bracket (25 pts), not the
        # top of the 1-10% bracket (10 pts).
        assert _gap_points(0.10) == 25

    def test_just_over_10_pct(self):
        assert _gap_points(0.1001) == 25

    def test_exactly_30_pct(self):
        assert _gap_points(0.30) == 25

    def test_just_over_30_pct(self):
        assert _gap_points(0.3001) == 40


class TestDaysPointsBoundaries:
    @pytest.mark.parametrize(
        "days,expected",
        [
            (8, 0),
            (7, 10),
            (4, 10),
            (3, 20),
            (1, 20),
            (0, 30),
        ],
    )
    def test_bands(self, days, expected):
        assert _days_points(days) == expected


class TestScoreToProbabilityBoundaries:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (0, 0.05),
            (10, 0.05),
            (11, 0.15),
            (25, 0.15),
            (26, 0.35),
            (45, 0.35),
            (46, 0.55),
            (65, 0.55),
            (66, 0.75),
            (85, 0.75),
            (86, 0.92),
            (200, 0.92),
        ],
    )
    def test_bands(self, score, expected):
        assert _score_to_probability(score) == expected


# ---------------------------------------------------------------------------
# Coverage gap: cap_amount never actually clamped a fine in the mock data.
# ---------------------------------------------------------------------------


class TestCapAmountActuallyClamps:
    def test_per_unit_fine_is_clamped_by_cap(self):
        rule = FineRule(
            rule_id="RULE-CAPPED",
            violation_type="SHORT_SHIP",
            calc_type=CalcType.PER_UNIT,
            rate=2.0,
            threshold_pct=0.0,
            cap_amount=500.0,
        )
        # 1000 unit shortfall x $2/unit = $2,000 uncapped -- must clamp to $500.
        fine = price_shortage_fine(rule, order_qty=1000, unit_price=10.0, shortfall_units=1000)
        assert fine == 500.0

    def test_percent_of_po_fine_is_clamped_by_cap(self):
        rule = FineRule(
            rule_id="RULE-CAPPED-2",
            violation_type="FILL_RATE",
            calc_type=CalcType.PERCENT_OF_PO,
            rate=0.50,
            threshold_pct=0.03,
            cap_amount=1000.0,
        )
        # 50% of a $10,000 PO = $5,000 uncapped -- must clamp to $1,000.
        fine = price_shortage_fine(rule, order_qty=1000, unit_price=10.0, shortfall_units=500)
        assert fine == 1000.0

    def test_delay_fine_is_clamped_by_cap(self):
        rule = FineRule(
            rule_id="RULE-CAPPED-3",
            violation_type="OTIF_LATE",
            calc_type=CalcType.PERCENT_OF_PO,
            rate=0.50,
            cap_amount=1000.0,
        )
        fine = price_delay_fine(rule, order_qty=1000, unit_price=10.0)
        assert fine == 1000.0

    def test_uncapped_fine_below_cap_is_unaffected(self):
        rule = FineRule(
            rule_id="RULE-CAPPED-4",
            violation_type="SHORT_SHIP",
            calc_type=CalcType.PER_UNIT,
            rate=2.0,
            threshold_pct=0.0,
            cap_amount=5000.0,
        )
        fine = price_shortage_fine(rule, order_qty=1000, unit_price=10.0, shortfall_units=100)
        assert fine == 200.0  # well under the cap -- should NOT be clamped


# ---------------------------------------------------------------------------
# Coverage gap: the locked-in shortage override was only ever exercised via
# a PERCENT_OF_PO rule (Amazon). Prove it also works with PER_UNIT (Walmart).
# ---------------------------------------------------------------------------


class TestShortageLockedInAppliesRegardlessOfCalcType:
    def test_locked_in_probability_with_per_unit_rule(self):
        rule = FineRule(
            rule_id="RULE-WMT-SHORT",
            violation_type="SHORT_SHIP",
            calc_type=CalcType.PER_UNIT,
            rate=2.0,
            threshold_pct=0.02,
            cap_amount=5000,
        )
        snap = make_snapshot(
            confirmed_qty=900,  # permanent 100-unit shortfall
            actual_ship_date=TODAY,  # physically shipped, locked in
            production_status=ProductionStatus.BEHIND,
        )
        result = project_order(snap, [rule])
        assert result.shortage_probability == SHORTAGE_LOCKED_IN_PROBABILITY
        # 100 units short, 2% threshold (20 units) -> 80 penalized x $2 = $160
        assert result.violations[0].fine_if_realized == 160.0

    def test_no_lock_in_without_actual_ship_date(self):
        # No rule needed here -- compute_shortage_probability doesn't take
        # one; only project_order (tested above) does.
        snap = make_snapshot(
            confirmed_qty=900, actual_ship_date=None, production_status=ProductionStatus.BEHIND
        )
        prob = compute_shortage_probability(snap)
        assert prob != SHORTAGE_LOCKED_IN_PROBABILITY

    def test_no_lock_in_when_shortfall_is_zero_even_if_shipped(self):
        snap = make_snapshot(confirmed_qty=1000, actual_ship_date=TODAY)  # full qty, shipped
        prob = compute_shortage_probability(snap)
        assert prob != SHORTAGE_LOCKED_IN_PROBABILITY


# ---------------------------------------------------------------------------
# Coverage gap: stacking_mode="MAX" was never actually invoked anywhere.
# ---------------------------------------------------------------------------


class TestStackingModes:
    def _two_rules(self):
        return [
            FineRule("R-SHORT", "SHORT_SHIP", CalcType.PER_UNIT, rate=2.0, threshold_pct=0.0),
            FineRule("R-OTIF", "OTIF_LATE", CalcType.FLAT_FEE, rate=500.0),
        ]

    def test_sum_adds_all_violations(self):
        snap = make_snapshot(confirmed_qty=900)  # guarantees a nonzero shortage fine
        result = project_order(snap, self._two_rules(), stacking_mode="SUM")
        expected_total = sum(v.expected_fine for v in result.violations)
        assert result.total_expected_fine == expected_total
        assert len(result.violations) == 2

    def test_max_takes_only_the_larger_violation(self):
        snap = make_snapshot(confirmed_qty=900)
        result = project_order(snap, self._two_rules(), stacking_mode="MAX")
        assert result.total_expected_fine == max(v.expected_fine for v in result.violations)

    def test_invalid_stacking_mode_raises(self):
        snap = make_snapshot()
        with pytest.raises(ValueError):
            project_order(snap, self._two_rules(), stacking_mode="AVERAGE")


# ---------------------------------------------------------------------------
# The demand_exception finding: the review correctly noted it looked inert
# in the mock data. That was a timing artifact (days_points was 0 that day),
# not a mechanism failure. Prove both halves of that explicitly.
# ---------------------------------------------------------------------------


class TestDemandExceptionSignal:
    def test_inert_when_days_points_is_zero(self):
        # This reproduces exactly the mock-data condition the review flagged:
        # far from delivery, so days_points contributes 0, so +5 points from
        # the exception flag doesn't cross a band boundary either way.
        far_out = make_snapshot(requested_delivery_date=TODAY + timedelta(days=20))
        without_flag = compute_shortage_probability(far_out)
        with_flag = compute_shortage_probability(
            make_snapshot(requested_delivery_date=TODAY + timedelta(days=20), demand_exception_flagged=True)
        )
        assert without_flag == with_flag == 0.05

    def test_moves_the_needle_when_closer_to_delivery(self):
        # Same flag, but placed where days_points is already nonzero (6 days
        # out -> 10 pts) so the +5 crosses the 10/11 band boundary.
        closer = make_snapshot(requested_delivery_date=TODAY + timedelta(days=6))
        without_flag = compute_shortage_probability(closer)
        with_flag = compute_shortage_probability(
            make_snapshot(requested_delivery_date=TODAY + timedelta(days=6), demand_exception_flagged=True)
        )
        assert without_flag == 0.05
        assert with_flag == 0.15
        assert with_flag != without_flag

    def test_superseded_once_status_escalates(self):
        # The flag should stop mattering once production status itself
        # reflects the risk -- it's not meant to double-count.
        snap = make_snapshot(
            requested_delivery_date=TODAY + timedelta(days=6),
            production_status=ProductionStatus.AT_RISK,
            demand_exception_flagged=True,
        )
        assert _demand_exception_points(snap) == 0


# ---------------------------------------------------------------------------
# Gap 2 fix: production status must now feed the delay model as a fallback
# leading indicator, but only when no more specific signal exists.
# ---------------------------------------------------------------------------


class TestProductionStatusDelayCoupling:
    def test_on_track_production_does_not_shift_ship_date(self):
        snap = make_snapshot(production_status=ProductionStatus.ON_TRACK)
        assert resolve_expected_ship_date(snap) == snap.required_ship_date

    def test_at_risk_production_adds_one_day_anticipatory_slip(self):
        snap = make_snapshot(production_status=ProductionStatus.AT_RISK)
        assert resolve_expected_ship_date(snap) == snap.required_ship_date + timedelta(days=1)

    def test_behind_production_adds_two_day_anticipatory_slip(self):
        snap = make_snapshot(production_status=ProductionStatus.BEHIND)
        assert resolve_expected_ship_date(snap) == snap.required_ship_date + timedelta(days=2)

    def test_behind_production_raises_delay_probability_above_baseline(self):
        on_track = make_snapshot(production_status=ProductionStatus.ON_TRACK)
        behind = make_snapshot(production_status=ProductionStatus.BEHIND)
        assert compute_delay_probability(behind) > compute_delay_probability(on_track)

    def test_actual_ship_date_overrides_production_status_assumption(self):
        # A real fact beats a generic status-based guess, even if status
        # is still BEHIND at the time of shipment.
        snap = make_snapshot(
            production_status=ProductionStatus.BEHIND,
            actual_ship_date=TODAY,
        )
        assert resolve_expected_ship_date(snap) == TODAY

    def test_explicit_expected_ship_date_override_beats_production_status(self):
        real_reschedule = TODAY + timedelta(days=5)
        snap = make_snapshot(
            production_status=ProductionStatus.BEHIND,
            expected_ship_date=real_reschedule,
        )
        assert resolve_expected_ship_date(snap) == real_reschedule

    def test_missed_appointment_beats_production_status_assumption(self):
        # A concrete missed-appointment event is more specific than a
        # generic production-status guess, even if status is only AT_RISK.
        snap = make_snapshot(
            production_status=ProductionStatus.AT_RISK,
            appointment_status=AppointmentStatus.MISSED,
        )
        assert resolve_expected_ship_date(snap) == snap.required_ship_date + timedelta(days=1)


# ---------------------------------------------------------------------------
# Full four-scenario regression -- locks in the numbers shown in the design
# documentation and mock seed data so a future change can't silently drift.
# ---------------------------------------------------------------------------


class TestFourScenarioRegression:
    def test_wmt_100234_appointment_missed_day(self):
        from app.engine.scenario_data import WMT_RULES, wmt_days

        snap, _ = wmt_days[7]  # Aug 9: appointment MISSED
        result = project_order(snap, WMT_RULES)
        delay = next(v for v in result.violations if v.violation_type == "OTIF_LATE")
        assert delay.probability == 0.50
        assert delay.expected_fine == 540.00

    def test_amz_778501_locks_in_on_ship_day(self):
        from app.engine.scenario_data import AMZ_RULES, amz1_days

        snap, _ = amz1_days[9]  # Aug 12: ships short, on schedule
        result = project_order(snap, AMZ_RULES)
        shortage = next(v for v in result.violations if v.violation_type == "FILL_RATE")
        delay = next(v for v in result.violations if v.violation_type == "OTIF_LATE")
        assert shortage.probability == SHORTAGE_LOCKED_IN_PROBABILITY
        assert delay.probability == pytest.approx(0.024, abs=0.001)  # false-alarm resolution


# ---------------------------------------------------------------------------
# New in response to second review round: threshold unit mismatch,
# TIERED calc type, and ASN_LATE were all named as landmines. These prove
# the fixes actually work rather than just documenting intent.
# ---------------------------------------------------------------------------


class TestThresholdPctUnitGuard:
    def test_valid_fraction_is_accepted(self):
        rule = FineRule("R1", "SHORT_SHIP", CalcType.PER_UNIT, rate=2.0, threshold_pct=0.02)
        assert rule.threshold_pct == 0.02

    def test_whole_number_percent_is_rejected(self):
        # This is exactly the 100x bug the review warned about: someone
        # loads 2.0 (meaning "2%" in whole-number convention) where a
        # fraction (0.02) was expected. Must fail loudly, not silently
        # produce a rule that's 100x too aggressive.
        with pytest.raises(ValueError, match="threshold_pct"):
            FineRule("R1", "SHORT_SHIP", CalcType.PER_UNIT, rate=2.0, threshold_pct=2.0)

    def test_negative_threshold_is_rejected(self):
        with pytest.raises(ValueError):
            FineRule("R1", "SHORT_SHIP", CalcType.PER_UNIT, rate=2.0, threshold_pct=-0.01)


class TestTieredCalcType:
    def _tiered_rule(self):
        return FineRule(
            rule_id="R-TIERED",
            violation_type="FILL_RATE",
            calc_type=CalcType.TIERED,
            threshold_pct=0.0,
            tiers=[
                FineTier(0.00, 0.10, 0.02),  # 0-10% short -> 2% of PO
                FineTier(0.10, 0.30, 0.05),  # 10-30% short -> 5% of PO
                FineTier(0.30, 1.01, 0.10),  # 30%+ short -> 10% of PO
            ],
        )

    def test_requires_tiers(self):
        with pytest.raises(ValueError, match="tiers"):
            FineRule("R1", "FILL_RATE", CalcType.TIERED, threshold_pct=0.0)

    def test_lowest_band_applies(self):
        rule = self._tiered_rule()
        # 5% shortfall on a $10,000 PO -> lowest band, 2%
        fine = price_shortage_fine(rule, order_qty=1000, unit_price=10.0, shortfall_units=50)
        assert fine == pytest.approx(200.0)  # 0.02 x $10,000

    def test_middle_band_applies(self):
        rule = self._tiered_rule()
        # 20% shortfall -> middle band, 5%
        fine = price_shortage_fine(rule, order_qty=1000, unit_price=10.0, shortfall_units=200)
        assert fine == pytest.approx(500.0)

    def test_top_band_applies(self):
        rule = self._tiered_rule()
        # 50% shortfall -> top band, 10%
        fine = price_shortage_fine(rule, order_qty=1000, unit_price=10.0, shortfall_units=500)
        assert fine == pytest.approx(1000.0)

    def test_tiered_still_respects_cap(self):
        rule = self._tiered_rule()
        rule.cap_amount = 300.0
        fine = price_shortage_fine(rule, order_qty=1000, unit_price=10.0, shortfall_units=500)
        assert fine == 300.0

    def test_tiered_not_yet_supported_for_delay_rules(self):
        # Honest, explicit failure -- not yet built, and it says so.
        rule = FineRule("R1", "OTIF_LATE", CalcType.TIERED, threshold_pct=0.0, tiers=[FineTier(0, 1, 0.5)])
        with pytest.raises(NotImplementedError):
            price_delay_fine(rule, order_qty=1000, unit_price=10.0)


class TestAsnLateMapping:
    def test_asn_late_routes_through_delay_model_without_crashing(self):
        rule = FineRule("R-ASN", "ASN_LATE", CalcType.FLAT_FEE, rate=250.0)
        snap = make_snapshot(appointment_status=AppointmentStatus.MISSED)
        result = project_order(snap, [rule])
        assert result.violations[0].violation_type == "ASN_LATE"
        assert result.violations[0].probability == compute_delay_probability(snap)
