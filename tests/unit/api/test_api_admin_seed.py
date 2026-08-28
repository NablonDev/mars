"""
Exercises the two admin endpoints -- "seed via API" and the full
day-by-day replay -- and checks the results against the numbers already
published and cross-verified in data/samples/mars_fines_mock_seed_data.sql.
If these ever drift, it means either the engine changed (expected --
update the docs too) or the API/service wiring broke something the
engine itself gets right (a real regression).
"""

from datetime import date

from sqlalchemy import func, select

from app.models import JobItem, PoDeliveryChangeRequest, Retailer
from app.models.enums import JobRunType, JobTaskType
from app.repositories.job_queue import JobQueueRepository
from app.services.seeding.fine_projection import calendar_offset


def test_seed_master_data_is_idempotent(client):
    first = client.post("/api/v1/admin/seed-master-data")
    assert first.status_code == 200
    assert first.json() == {
        "retailers": 2,
        "skus": 3,
        "locations": 2,
        "carriers": 2,
        "rules": 4,
        "orders": 4,
        "mitigation_inputs": 3,
    }

    second = client.post("/api/v1/admin/seed-master-data")
    assert second.status_code == 200
    assert second.json() == {
        "retailers": 0,
        "skus": 0,
        "locations": 0,
        "carriers": 0,
        "rules": 0,
        "orders": 0,
        "mitigation_inputs": 0,
    }


def test_seeded_orders_are_listable(seeded_client):
    resp = seeded_client.get("/api/v1/orders")
    assert resp.status_code == 200
    order_ids = {o["order_id"] for o in resp.json()}
    assert order_ids == {"WMT-100234", "WMT-100511", "AMZ-778501", "AMZ-780112"}


def test_simulate_daily_run_reproduces_published_numbers(seeded_client):
    offset = calendar_offset()
    resp = seeded_client.post("/api/v1/admin/simulate-daily-run")
    assert resp.status_code == 200
    scenarios = {s["order_id"]: s["days"] for s in resp.json()["scenarios"]}

    # WMT-100234, Aug 9: carrier misses the dock appointment. Unlike
    # tests/test_fine_engine.py's
    # TestFourScenarioRegression::test_wmt_100234_appointment_missed_day
    # (which projects the raw, unmodified OrderSnapshot directly through
    # the pure engine and still gets $540/0.50 -- that test is intentionally
    # isolated from Order-level state), this endpoint's Aug 9 number is
    # lower: WMT-100234 is the seeded PO delivery-change-request ACCEPTED
    # scenario (see app/services/seeding/fine_projection.py's
    # _NEGOTIATION_SCENARIOS) -- Walmart accepts a 3-day extension on Aug 6,
    # shifting current_required_ship_date from Aug 9 to Aug 12. By the time
    # the day-loop reaches Aug 9, `required_ship_date - projection_date` is
    # 3 days instead of 0, which drops the delay calc's stage from 3 to 2
    # (and thus the table lookup from 0.50 to 0.30) even though the buffer
    # bucket itself (eq_-1) is unchanged -- see
    # tests/unit/services/test_seeding_po_delivery_change.py for the
    # assertion that isolates this effect precisely.
    wmt_aug9 = next(
        d for d in scenarios["WMT-100234"] if d["projection_date"] == (date(2026, 8, 9) + offset).isoformat()
    )
    assert wmt_aug9["total_expected_fine"] == 324.00
    assert wmt_aug9["delay_probability"] == 0.30
    # Per-model breakdown (not just the combined total) -- fully recovered
    # by this day, so the shortage side contributes nothing; the whole
    # $324 is the delay side.
    assert wmt_aug9["shortage_fine"] == 0.0
    assert wmt_aug9["delay_fine"] == 324.00
    assert wmt_aug9["shortage_fine"] + wmt_aug9["delay_fine"] == wmt_aug9["total_expected_fine"]

    # AMZ-778501, Aug 12: ships short, on schedule -- shortage locks in at 95%.
    amz_aug12 = next(
        d for d in scenarios["AMZ-778501"] if d["projection_date"] == (date(2026, 8, 12) + offset).isoformat()
    )
    assert amz_aug12["shortage_probability"] == 0.95
    assert amz_aug12["shortage_fine"] == 319.20
    assert amz_aug12["delay_fine"] == 12.00

    # Orders are marked DELIVERED once their scenario finishes.
    orders_resp = seeded_client.get("/api/v1/orders")
    statuses = {o["order_id"]: o["order_status"] for o in orders_resp.json()}
    assert all(status == "DELIVERED" for status in statuses.values())


def test_simulate_daily_run_requires_seeding_first(client):
    """404, not the 400 this used to return: the unseeded case raises
    `OrderNotFoundError` (a `NotFoundError`), which the shared handler in
    app/core/exceptions.py maps to its own status. It used to fall through
    the removed catch-all `ValueError` -> 400 handler in main.py."""
    resp = client.post("/api/v1/admin/simulate-daily-run")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ORDER_NOT_FOUND"
    assert "seed_master_data" in resp.json()["error"]["message"]


def test_simulate_daily_run_no_longer_crashes_on_a_second_call(seeded_client):
    """Regression test for a real bug found running this against a real
    (persistent, not in-memory) Postgres database: calling this twice in
    a row used to raise a raw 500 -- IntegrityError, duplicate key on
    confirmation_id/production_id/shipment_id/exception_id, because
    those are deterministic natural keys ("CONF-{order_id}-{date}", etc.)
    and the fact-writing methods used to insert unconditionally. The
    in-memory, fresh-per-test SQLite database used everywhere else could
    never expose this, because no other test calls this endpoint twice.
    Fixed in OrderRepository: each add_*/record_* method now checks for
    an existing row on its natural key and no-ops instead of inserting a
    duplicate.

    That fix stops the crash, but doesn't make every order's numbers
    perfectly stable across repeated calls -- see the next test."""
    first = seeded_client.post("/api/v1/admin/simulate-daily-run")
    assert first.status_code == 200

    second = seeded_client.post("/api/v1/admin/simulate-daily-run")
    assert second.status_code == 200
    third = seeded_client.post("/api/v1/admin/simulate-daily-run")
    assert third.status_code == 200


def test_repeat_calls_are_stable_except_for_the_known_shared_plant_limitation(seeded_client):
    """Precisely characterizes what the idempotency fix does and doesn't
    guarantee. AMZ-780112 is the only order that gives byte-identical
    numbers no matter how many times simulate-daily-run is called -- its
    seeded negotiation outcome is EXPIRED (see
    app/services/seeding/fine_projection.py's _NEGOTIATION_SCENARIOS),
    which never touches Order.current_delivery_date/current_required_ship_date,
    so nothing about its own projection inputs ever changes call to call.

    The other three orders are each stable from the *second* call onward,
    but call 1 differs from calls 2/3, for two unrelated reasons:

    - WMT-100234 (ACCEPTED) and WMT-100511 (COUNTERED) each shift their
      order's current_required_ship_date/current_delivery_date partway
      through call 1's own day-loop (Aug 6 and Aug 9 respectively). The
      idempotency guard (a prior po_delivery_change_request history row
      for that order) means calls 2+ never re-fire create_request/
      record_response -- but the shift itself already persisted on the
      Order row after call 1, so calls 2+ see the shifted dates from the
      very first day of that order's replay, not just from the day the
      negotiation originally resolved on. Call 1 is therefore the odd one
      out, not calls 2/3.
    - AMZ-778501 doesn't touch order dates at all (its seeded outcome is
      REJECTED), so this is exactly the pre-existing, unrelated
      shared-production-line limitation already documented in
      tests/test_known_limitations.py: it shares a production line
      (SKU-WHI20 @ LOC-COL) with AMZ-780112. On the very first call,
      AMZ-778501's Aug 11 projection is computed before AMZ-780112 has
      written any facts at all, so it correctly sees only its own BEHIND
      status. On every call after the first, AMZ-780112's facts (written
      during call 1 and never removed, only ever no-op'd afterward) are
      already sitting in that shared table the whole time, so AMZ-778501's
      Aug 11 re-projection now also sees AMZ-780112's eventual recovery --
      and gets a materially lower number as a result. It is not a new bug
      and not something this fix could or should paper over.
    """
    offset = calendar_offset()
    first = seeded_client.post("/api/v1/admin/simulate-daily-run").json()["scenarios"]
    second = seeded_client.post("/api/v1/admin/simulate-daily-run").json()["scenarios"]
    third = seeded_client.post("/api/v1/admin/simulate-daily-run").json()["scenarios"]

    by_order = {
        run_index: {s["order_id"]: s["days"] for s in run}
        for run_index, run in enumerate([first, second, third])
    }

    assert by_order[0]["AMZ-780112"] == by_order[1]["AMZ-780112"] == by_order[2]["AMZ-780112"], (
        "AMZ-780112 (EXPIRED outcome, no Order-date change) should reproduce "
        "identically no matter how many times this is called"
    )

    for shifting_order in ("WMT-100234", "WMT-100511", "AMZ-778501"):
        assert by_order[1][shifting_order] == by_order[2][shifting_order], (
            f"{shifting_order} should be stable from the second call onward"
        )
        assert by_order[0][shifting_order] != by_order[1][shifting_order], (
            f"{shifting_order}'s first call is expected to differ from later calls"
        )

    wmt_100234_aug5 = [
        next(
            d
            for d in by_order[i]["WMT-100234"]
            if d["projection_date"] == (date(2026, 8, 5) + offset).isoformat()
        )
        for i in range(3)
    ]
    assert wmt_100234_aug5[0]["total_expected_fine"] == 401.00
    assert wmt_100234_aug5[1]["total_expected_fine"] == wmt_100234_aug5[2]["total_expected_fine"] == 195.00

    wmt_100511_aug9 = [
        next(
            d
            for d in by_order[i]["WMT-100511"]
            if d["projection_date"] == (date(2026, 8, 9) + offset).isoformat()
        )
        for i in range(3)
    ]
    assert wmt_100511_aug9[0]["total_expected_fine"] == 243.00
    assert wmt_100511_aug9[1]["total_expected_fine"] == wmt_100511_aug9[2]["total_expected_fine"] == 40.50

    amz_aug11 = [
        next(
            d
            for d in by_order[i]["AMZ-778501"]
            if d["projection_date"] == (date(2026, 8, 11) + offset).isoformat()
        )
        for i in range(3)
    ]
    # First call: matches the published, canonical number -- unaffected by
    # this order's own (REJECTED) negotiation outcome, since REJECTED never
    # touches Order dates. Confirms the shared-plant limitation's numbers
    # are exactly what they were before this feature existed.
    assert amz_aug11[0]["shortage_probability"] == 0.75
    assert amz_aug11[0]["total_expected_fine"] == 672.0
    # Every call after that: stable at a different, lower number, because
    # AMZ-780112's facts are now visible the whole time it's projecting.
    assert amz_aug11[1] == amz_aug11[2]
    assert amz_aug11[1]["shortage_probability"] == 0.35
    assert amz_aug11[1] != amz_aug11[0]


def test_seed_master_data_force_false_is_still_idempotent(seeded_client):
    """force=False (explicit, not just the default) must behave exactly
    like today's idempotent seed: skip everything already present."""
    resp = seeded_client.post("/api/v1/admin/seed-master-data", params={"force": False})
    assert resp.status_code == 200
    assert resp.json() == {
        "retailers": 0,
        "skus": 0,
        "locations": 0,
        "carriers": 0,
        "rules": 0,
        "orders": 0,
        "mitigation_inputs": 0,
    }


def test_seed_master_data_force_true_truncates_and_reseeds_from_scratch(client, db_session):
    """force=True must fully reset a corrupted row rather than skip past
    it -- the defining difference from the default idempotent-insert
    behavior, which would leave the corruption in place forever."""
    first = client.post("/api/v1/admin/seed-master-data")
    assert first.status_code == 200

    retailer = db_session.scalars(select(Retailer).where(Retailer.retailer_id == "RET-WMT")).one()
    retailer.retailer_name = "CORRUPTED BY TEST"
    db_session.commit()

    forced = client.post("/api/v1/admin/seed-master-data", params={"force": True})
    assert forced.status_code == 200
    # A genuine reseed from scratch -- every count matches the very first
    # (non-idempotent-skip) call, not the all-zero idempotent-skip shape.
    assert forced.json() == {
        "retailers": 2,
        "skus": 3,
        "locations": 2,
        "carriers": 2,
        "rules": 4,
        "orders": 4,
        "mitigation_inputs": 3,
    }

    db_session.expire_all()
    restored = db_session.scalars(select(Retailer).where(Retailer.retailer_id == "RET-WMT")).one()
    assert restored.retailer_name == "Walmart"


def test_seed_master_data_force_true_survives_a_prior_simulate_daily_run(seeded_client, db_session):
    """force=True must clear order-dependent fact rows (order_confirmation,
    production_schedule, shipment, demand_exception, actual_fine, and
    mitigation_input) before truncating sales_order itself -- otherwise the
    FK from those tables into sales_order.order_id raises an
    IntegrityError instead of letting the reseed proceed."""
    run_resp = seeded_client.post("/api/v1/admin/simulate-daily-run")
    assert run_resp.status_code == 200

    forced = seeded_client.post("/api/v1/admin/seed-master-data", params={"force": True})
    assert forced.status_code == 200
    assert forced.json() == {
        "retailers": 2,
        "skus": 3,
        "locations": 2,
        "carriers": 2,
        "rules": 4,
        "orders": 4,
        "mitigation_inputs": 3,
    }

    orders_resp = seeded_client.get("/api/v1/orders")
    assert orders_resp.status_code == 200
    statuses = {o["order_id"]: o["order_status"] for o in orders_resp.json()}
    # Freshly reseeded orders are OPEN again, not left DELIVERED from the
    # simulate-daily-run call that happened before the force-reseed.
    assert all(status == "OPEN" for status in statuses.values())


def test_seed_master_data_force_true_survives_job_item_and_negotiation_history(seeded_client, db_session):
    """Regression test for a real bug found against a Postgres-backed stack:
    force=True used to truncate sales_order without first clearing job_item
    (FK'd via job_item.order_id) or po_delivery_change_request (FK'd via its
    own order_id), raising a raw ForeignKeyViolation/IntegrityError instead
    of letting the reseed proceed. SQLite (used by this whole suite) has no
    `PRAGMA foreign_keys` enabled, so it silently allows deleting sales_order
    out from under those child rows rather than raising -- the row-count
    assertions below are what actually catch the bug here, not an expected
    exception; on the real Postgres stack this same gap surfaces as the
    reported ForeignKeyViolation instead.

    simulate_daily_run() alone doesn't create job_item rows -- it drives
    FineProjectionService in-process rather than through the job queue --
    so this enqueues one directly against a seeded order, mirroring
    app/workers/fine_projection.py::enqueue_daily_run. simulate_daily_run()
    itself already exercises the po_delivery_change_request half via the
    seeded negotiation scenarios.

    Fixed in PoDeliveryChangeRequestRepository.truncate_all() and
    JobQueueRepository.truncate_all(), both now called from
    FineSeedingService._truncate_seeded_tables() before
    OrderRepository.truncate_all()."""
    run_resp = seeded_client.post("/api/v1/admin/simulate-daily-run")
    assert run_resp.status_code == 200
    assert db_session.scalar(select(func.count()).select_from(PoDeliveryChangeRequest)) > 0

    job_queue = JobQueueRepository(db_session)
    today = date.today()  # noqa: DTZ011 -- test date anchor, not a naive-datetime bug
    job_run = job_queue.create_run(
        run_type=JobRunType.MANUAL_BATCH,
        projection_date=today,
        triggered_by="test",
    )
    job_queue.enqueue(
        job_run_id=job_run["id"],
        order_id="WMT-100234",
        projection_date=today,
        task_type=JobTaskType.ORDER_RUN,
        max_attempts=5,
    )
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(JobItem)) > 0

    forced = seeded_client.post("/api/v1/admin/seed-master-data", params={"force": True})
    assert forced.status_code == 200
    assert forced.json() == {
        "retailers": 2,
        "skus": 3,
        "locations": 2,
        "carriers": 2,
        "rules": 4,
        "orders": 4,
        "mitigation_inputs": 3,
    }

    # A genuine "reseed from scratch" (per _truncate_seeded_tables's own
    # docstring) leaves no orphaned job_item/po_delivery_change_request rows
    # dangling from the pre-reseed order history.
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(JobItem)) == 0
    assert db_session.scalar(select(func.count()).select_from(PoDeliveryChangeRequest)) == 0
