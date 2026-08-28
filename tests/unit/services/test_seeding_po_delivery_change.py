"""Tests for the PO delivery-date change request/response demo data interleaved
into `app.services.seeding.fine_projection.simulate_daily_run` -- one outcome
(ACCEPTED/COUNTERED/REJECTED/EXPIRED) per worked-example order, plus the
per-retailer extension-policy seed data in `app.services.seeding.master_data`
that drives it. See `_NEGOTIATION_SCENARIOS` in
app/services/seeding/fine_projection.py for the exact days/decisions.
"""

from datetime import date, timedelta

from app.services.seeding.fine_projection import calendar_offset


def test_all_four_negotiation_outcomes_wired_correctly(services):
    offset = calendar_offset()
    services.seeding_service.seed_master_data()
    services.seeding_service.simulate_daily_run()

    # WMT-100234: SHORTAGE -> ACCEPTED. current_delivery_date shifts
    # Aug 11 -> Aug 14, current_required_ship_date by the same +3-day
    # delta, Aug 9 -> Aug 12.
    wmt1 = services.orders.require_order("WMT-100234")
    assert wmt1["negotiation_status"] == "ACCEPTED"
    assert wmt1["current_delivery_date"] == date(2026, 8, 14) + offset
    assert wmt1["current_required_ship_date"] == date(2026, 8, 12) + offset

    # WMT-100511: DELAY -> COUNTERED. current_delivery_date shifts to the
    # countered Aug 17 (not the originally proposed Aug 19),
    # current_required_ship_date by the same +2-day delta, Aug 13 -> Aug 15.
    wmt2 = services.orders.require_order("WMT-100511")
    assert wmt2["negotiation_status"] == "COUNTERED"
    assert wmt2["current_delivery_date"] == date(2026, 8, 17) + offset
    assert wmt2["current_required_ship_date"] == date(2026, 8, 15) + offset

    # AMZ-778501: SHORTAGE -> REJECTED. No shadow tracking -- Order dates
    # stay untouched, the daily projection continues against the original
    # (unmodified) dates.
    amz1 = services.orders.require_order("AMZ-778501")
    assert amz1["negotiation_status"] == "REJECTED"
    assert amz1["current_delivery_date"] is None
    assert amz1["current_required_ship_date"] is None

    # AMZ-780112: OTHER (QA hold) -> never responded to, swept to EXPIRED.
    # Also untouched -- expire_stale never writes Order dates.
    amz2 = services.orders.require_order("AMZ-780112")
    assert amz2["negotiation_status"] == "EXPIRED"
    assert amz2["current_delivery_date"] is None
    assert amz2["current_required_ship_date"] is None

    history = services.po_delivery_change_requests.list_history("AMZ-780112")
    assert len(history) == 1
    assert history[0]["status"] == "EXPIRED"
    assert history[0]["reason_code"] == "OTHER"
    assert history[0]["retailer_response_date"] is None


def test_wmt_100234_accepted_reprojects_aug9_without_the_original_delay_number(services):
    """The Aug 9 day (carrier misses the dock appointment) is re-projected
    against the shifted current_required_ship_date (Aug 12, not the
    original Aug 9) by the time the day-loop reaches it -- required_ship_date
    minus projection_date goes from 0 days to 3 days, which drops the delay
    calc's stage from 3 to 2 (DELAY_PROBABILITY_TABLE['eq_-1']), even though
    the buffer bucket itself is unchanged. Confirms the projection genuinely
    reflects the new committed date, not just that some number changed."""
    offset = calendar_offset()
    services.seeding_service.seed_master_data()
    services.seeding_service.simulate_daily_run()

    history = services.projections.list_history("WMT-100234")
    aug9_delay_rows = [
        r
        for r in history
        if r["projection_date"] == date(2026, 8, 9) + offset and r["violation_type"] == "OTIF_LATE"
    ]
    assert len(aug9_delay_rows) == 1
    assert aug9_delay_rows[0]["failure_probability"] == 0.30
    assert aug9_delay_rows[0]["projected_fine_amount"] == 324.00


def test_per_retailer_extension_policy_differentiation(services):
    """Walmart and Amazon get different extension_min_lead_days/
    extension_response_sla_hours/extension_fine_threshold seed values --
    proves the seeded retailers aren't falling back to Retailer's column
    defaults, and that the difference actually reaches the SLA on a real
    request (Amazon's shorter 24h SLA vs. Walmart's 48h)."""
    services.seeding_service.seed_master_data()

    wmt_policy = services.master_data.get_extension_policy("RET-WMT")
    amz_policy = services.master_data.get_extension_policy("RET-AMZ")

    assert wmt_policy == {"min_lead_days": 2, "response_sla_hours": 48, "fine_threshold": 200.0}
    assert amz_policy == {"min_lead_days": 2, "response_sla_hours": 24, "fine_threshold": 100.0}

    services.seeding_service.simulate_daily_run()

    wmt_request = services.po_delivery_change_requests.list_history("WMT-100234")[0]
    amz_request = services.po_delivery_change_requests.list_history("AMZ-778501")[0]

    assert wmt_request["expires_at"] - wmt_request["requested_at"] == timedelta(hours=48)
    assert amz_request["expires_at"] - amz_request["requested_at"] == timedelta(hours=24)


def test_simulate_daily_run_return_value_includes_negotiation_outcome(services):
    """Each order's summary dict carries its negotiation outcome (the
    PoDeliveryChangeRequestRepository row from create/respond/expire),
    demonstrating the fine impact alongside the existing day-by-day trend.
    This key is intentionally not part of SimulateDailyRunResponse (the
    HTTP contract stays whatever it already was) -- only the direct
    service-layer return value carries it."""
    services.seeding_service.seed_master_data()
    result = services.seeding_service.simulate_daily_run()

    by_order = {s["order_id"]: s for s in result}
    assert by_order["WMT-100234"]["negotiation"]["status"] == "ACCEPTED"
    assert by_order["WMT-100511"]["negotiation"]["status"] == "COUNTERED"
    assert by_order["AMZ-778501"]["negotiation"]["status"] == "REJECTED"
    assert by_order["AMZ-780112"]["negotiation"]["status"] == "EXPIRED"


def test_simulate_daily_run_negotiation_is_idempotent(services):
    """Re-running simulate_daily_run must not double-create a negotiation
    request or error retrying a terminal one -- same convention as
    seed()'s skip-if-exists idempotency."""
    offset = calendar_offset()
    services.seeding_service.seed_master_data()
    services.seeding_service.simulate_daily_run()
    services.seeding_service.simulate_daily_run()
    services.seeding_service.simulate_daily_run()

    for order_id in ("WMT-100234", "WMT-100511", "AMZ-778501", "AMZ-780112"):
        history = services.po_delivery_change_requests.list_history(order_id)
        assert len(history) == 1, (
            f"{order_id} should have exactly one negotiation request, got {len(history)}"
        )

    # And the negotiation outcome itself is unaffected by the repeat calls.
    wmt1 = services.orders.require_order("WMT-100234")
    assert wmt1["negotiation_status"] == "ACCEPTED"
    assert wmt1["current_delivery_date"] == date(2026, 8, 14) + offset
