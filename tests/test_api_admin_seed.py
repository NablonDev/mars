"""
Exercises the two admin endpoints -- "seed via API" and the full
day-by-day replay -- and checks the results against the numbers already
published and cross-verified in data/samples/mars_fines_mock_seed_data.sql and
docs/FINE_ENGINE.md. If these ever drift, it means either the engine
changed (expected -- update the docs too) or the API/service wiring
broke something the engine itself gets right (a real regression).
"""


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
    }


def test_seeded_orders_are_listable(seeded_client):
    resp = seeded_client.get("/api/v1/orders")
    assert resp.status_code == 200
    order_ids = {o["order_id"] for o in resp.json()}
    assert order_ids == {"WMT-100234", "WMT-100511", "AMZ-778501", "AMZ-780112"}


def test_simulate_daily_run_reproduces_published_numbers(seeded_client):
    resp = seeded_client.post("/api/v1/admin/simulate-daily-run")
    assert resp.status_code == 200
    scenarios = {s["order_id"]: s["days"] for s in resp.json()["scenarios"]}

    # WMT-100234, Aug 9: carrier misses the dock appointment -- matches
    # docs/FINE_ENGINE.md section 5 and tests/test_fine_engine.py's
    # TestFourScenarioRegression::test_wmt_100234_appointment_missed_day.
    wmt_aug9 = next(d for d in scenarios["WMT-100234"] if d["projection_date"] == "2026-08-09")
    assert wmt_aug9["total_expected_fine"] == 540.00
    assert wmt_aug9["delay_probability"] == 0.50
    # Per-model breakdown (not just the combined total) -- fully recovered
    # by this day, so the shortage side contributes nothing; the whole
    # $540 is the delay side.
    assert wmt_aug9["shortage_fine"] == 0.0
    assert wmt_aug9["delay_fine"] == 540.00
    assert wmt_aug9["shortage_fine"] + wmt_aug9["delay_fine"] == wmt_aug9["total_expected_fine"]

    # AMZ-778501, Aug 12: ships short, on schedule -- shortage locks in at 95%.
    amz_aug12 = next(d for d in scenarios["AMZ-778501"] if d["projection_date"] == "2026-08-12")
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
    guarantee. WMT-100234, WMT-100511, and AMZ-780112 give byte-identical
    numbers no matter how many times simulate-daily-run is called. Only
    AMZ-778501 doesn't -- and only because it shares a production line
    (SKU-WHI20 @ LOC-COL) with AMZ-780112 (see tests/test_known_limitations.py).
    On the very first call, AMZ-778501's Aug 11 projection is computed
    before AMZ-780112 has written any facts at all, so it correctly sees
    only its own BEHIND status. On every call after the first, AMZ-780112's
    facts (written during call 1 and never removed, only ever no-op'd
    afterward) are already sitting in that shared table the whole time,
    so AMZ-778501's Aug 11 re-projection now also sees AMZ-780112's
    eventual recovery -- and gets a materially lower number as a result.
    It then stays at that second, lower number forever after -- itself
    stable, just different from the very first run. This is the same
    documented mock-data limitation as before, showing up a second way;
    it is not a new bug and not something this fix could or should paper
    over -- see docs/FINE_ENGINE.md "Open items"."""
    first = seeded_client.post("/api/v1/admin/simulate-daily-run").json()["scenarios"]
    second = seeded_client.post("/api/v1/admin/simulate-daily-run").json()["scenarios"]
    third = seeded_client.post("/api/v1/admin/simulate-daily-run").json()["scenarios"]

    by_order = {
        run_index: {s["order_id"]: s["days"] for s in run}
        for run_index, run in enumerate([first, second, third])
    }

    for stable_order in ("WMT-100234", "WMT-100511", "AMZ-780112"):
        assert by_order[0][stable_order] == by_order[1][stable_order] == by_order[2][stable_order], (
            f"{stable_order} should reproduce identically no matter how many times this is called"
        )

    amz_aug11 = [
        next(d for d in by_order[i]["AMZ-778501"] if d["projection_date"] == "2026-08-11") for i in range(3)
    ]
    # First call: matches the published, canonical number (docs/FINE_ENGINE.md).
    assert amz_aug11[0]["shortage_probability"] == 0.75
    assert amz_aug11[0]["total_expected_fine"] == 672.0
    # Every call after that: stable at a different, lower number, because
    # AMZ-780112's facts are now visible the whole time it's projecting.
    assert amz_aug11[1] == amz_aug11[2]
    assert amz_aug11[1]["shortage_probability"] == 0.35
    assert amz_aug11[1] != amz_aug11[0]
