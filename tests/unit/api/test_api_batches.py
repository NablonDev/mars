"""Integration tests for the batch observability endpoints
(POST /batches/run, GET /batches/{id}, GET /batches/{id}/items).
"""

from uuid import UUID

from app.core.config import Settings, get_settings
from app.repositories.job_queue import JobQueueRepository

_ALL_FOUR_OPEN_ORDER_IDS = {"WMT-100234", "WMT-100511", "AMZ-778501", "AMZ-780112"}


def test_trigger_batch_run_enqueues_one_item_per_open_order_and_executes_nothing_inline(
    seeded_client, db_session
):
    resp = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["requested_item_count"] == 4
    job_run_id = body["job_run_id"]

    items_resp = seeded_client.get(f"/api/v1/batches/{job_run_id}/items", params={"limit": 200})
    assert items_resp.status_code == 200
    items = items_resp.json()["items"]
    assert {item["order_id"] for item in items} == _ALL_FOUR_OPEN_ORDER_IDS
    assert all(item["task_type"] == "ORDER_RUN" for item in items)
    assert all(item["status"] == "PENDING" for item in items)

    # Enqueue-only: nothing was executed inline. No projection exists for
    # any of these orders as a side effect of the trigger call.
    for order_id in _ALL_FOUR_OPEN_ORDER_IDS:
        exposure = seeded_client.get(f"/api/v1/orders/{order_id}/exposure")
        assert exposure.status_code == 422, exposure.text


def test_get_batch_status_counts_and_is_complete(seeded_client, db_session):
    resp = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    job_run_id = resp.json()["job_run_id"]

    status_resp = seeded_client.get(f"/api/v1/batches/{job_run_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["job_run_id"] == job_run_id
    assert body["requested_item_count"] == 4
    assert body["counts"] == {"PENDING": 4, "RUNNING": 0, "SUCCEEDED": 0, "DEAD": 0}
    assert body["total_items"] == 4
    assert body["is_complete"] is False

    # Simulate a worker finishing all 4 items: 3 succeed, 1 dies.
    repo = JobQueueRepository(db_session)
    items = repo.list_run_items(UUID(job_run_id))
    assert len(items) == 4
    for item in items[:3]:
        claimed = repo.claim_batch("worker-1", limit=1, job_item_ids=[item["id"]])
        assert claimed
        repo.mark_succeeded(item["id"], "worker-1")
    claimed = repo.claim_batch("worker-1", limit=1, job_item_ids=[items[3]["id"]])
    assert claimed
    repo.mark_dead(items[3]["id"], "worker-1", "boom", "SOME_FAILURE")
    db_session.commit()

    final_resp = seeded_client.get(f"/api/v1/batches/{job_run_id}")
    final_body = final_resp.json()
    assert final_body["counts"] == {"PENDING": 0, "RUNNING": 0, "SUCCEEDED": 3, "DEAD": 1}
    assert final_body["is_complete"] is True


def test_get_batch_status_404s_for_unknown_run(seeded_client):
    resp = seeded_client.get("/api/v1/batches/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_list_batch_items_filters_by_status(seeded_client, db_session):
    resp = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    job_run_id = resp.json()["job_run_id"]

    repo = JobQueueRepository(db_session)
    items = repo.list_run_items(UUID(job_run_id))
    dead_item_id = items[0]["id"]
    repo.claim_batch("worker-1", limit=1, job_item_ids=[dead_item_id])
    repo.mark_dead(dead_item_id, "worker-1", "internal secret detail XYZ", "PROJECTION_FAILED")
    db_session.commit()

    dead_resp = seeded_client.get(f"/api/v1/batches/{job_run_id}/items", params={"status": "DEAD"})
    assert dead_resp.status_code == 200
    dead_items = dead_resp.json()["items"]
    assert len(dead_items) == 1
    assert dead_items[0]["id"] == str(dead_item_id)
    assert dead_items[0]["last_error_code"] == "PROJECTION_FAILED"
    assert dead_items[0]["last_error_message"] == "Job failed with error code PROJECTION_FAILED"

    # Raw last_error internals must never appear in the response body.
    assert "internal secret detail XYZ" not in dead_resp.text

    pending_resp = seeded_client.get(f"/api/v1/batches/{job_run_id}/items", params={"status": "PENDING"})
    assert len(pending_resp.json()["items"]) == 3


def test_list_batch_items_paginates(seeded_client):
    resp = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    job_run_id = resp.json()["job_run_id"]

    page1 = seeded_client.get(f"/api/v1/batches/{job_run_id}/items", params={"limit": 2, "offset": 0})
    page2 = seeded_client.get(f"/api/v1/batches/{job_run_id}/items", params={"limit": 2, "offset": 2})

    assert page1.status_code == 200 and page2.status_code == 200
    ids_page1 = {item["id"] for item in page1.json()["items"]}
    ids_page2 = {item["id"] for item in page2.json()["items"]}
    assert len(ids_page1) == 2
    assert len(ids_page2) == 2
    assert ids_page1.isdisjoint(ids_page2)


def test_list_batch_items_invalid_status_is_422(seeded_client):
    resp = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    job_run_id = resp.json()["job_run_id"]

    resp = seeded_client.get(f"/api/v1/batches/{job_run_id}/items", params={"status": "NOT_A_STATUS"})
    assert resp.status_code == 422


def test_trigger_batch_run_defaults_projection_date_to_today(seeded_client):
    resp = seeded_client.post("/api/v1/batches/run", json={})
    assert resp.status_code == 202, resp.text
    assert resp.json()["requested_item_count"] == 4


def test_second_batch_run_owns_no_items_while_the_first_is_still_in_flight(seeded_client):
    """A manual batch fired while an earlier run is still draining must not
    claim, count, or re-dispatch the earlier run's items.

    `enqueue` returns the pre-existing in-flight row on collision, and that
    row belongs to the first run -- so a naive `if item is not None` would
    make the second run report 4 items it does not own and never reconcile
    to complete.
    """
    first = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    assert first.json()["requested_item_count"] == 4

    second = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    assert second.status_code == 202
    assert second.json()["requested_item_count"] == 0

    body = seeded_client.get(f"/api/v1/batches/{second.json()['job_run_id']}").json()
    assert body["requested_item_count"] == 0
    assert body["total_items"] == 0
    # Zero items is "never started", not "finished".
    assert body["is_complete"] is False

    # The first run still owns all four.
    first_body = seeded_client.get(f"/api/v1/batches/{first.json()['job_run_id']}").json()
    assert first_body["total_items"] == 4


def test_batch_run_completion_reconciles_after_a_partial_re_run(seeded_client, db_session):
    """Re-running after a partial failure enqueues only the orders that are
    no longer in flight, and that run completes on its own item set."""
    first = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    first_run_id = UUID(first.json()["job_run_id"])

    repo = JobQueueRepository(db_session)
    items = repo.list_run_items(first_run_id)
    repo.claim_batch("worker-1", limit=1, job_item_ids=[items[0]["id"]])
    repo.mark_succeeded(items[0]["id"], "worker-1")
    db_session.commit()

    second = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    second_run_id = second.json()["job_run_id"]
    # Only the finished order is re-enqueueable; the other three are still in flight.
    assert second.json()["requested_item_count"] == 1

    body = seeded_client.get(f"/api/v1/batches/{second_run_id}").json()
    assert body["total_items"] == 1
    assert body["is_complete"] is False

    new_items = repo.list_run_items(UUID(second_run_id))
    assert len(new_items) == 1
    repo.claim_batch("worker-2", limit=1, job_item_ids=[new_items[0]["id"]])
    repo.mark_succeeded(new_items[0]["id"], "worker-2")
    db_session.commit()

    final = seeded_client.get(f"/api/v1/batches/{second_run_id}").json()
    assert final["counts"]["SUCCEEDED"] == 1
    assert final["is_complete"] is True


def test_trigger_batch_run_reports_postgres_dispatch_mode_and_execution_note(seeded_client):
    """Default backend (this fixture's job_queue is built with a plain
    Settings(), which defaults job_queue_backend="postgres" -- see
    conftest.py's job_queue fixture)."""
    resp = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["dispatch_mode"] == "postgres"
    assert body["execution_note"] == "Enqueued; will be processed by the next scheduled batch drain."
    # Existing fields unchanged.
    assert body["requested_item_count"] == 4
    assert "job_run_id" in body


def test_trigger_batch_run_reports_service_bus_dispatch_mode_and_execution_note(seeded_client):
    seeded_client.app.dependency_overrides[get_settings] = lambda: Settings(job_queue_backend="service_bus")
    try:
        resp = seeded_client.post("/api/v1/batches/run", json={"projection_date": "2026-08-02"})
    finally:
        seeded_client.app.dependency_overrides.pop(get_settings, None)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["dispatch_mode"] == "service_bus"
    assert body["execution_note"] == "Dispatched to the queue; a consumer will pick it up when available."
    assert body["requested_item_count"] == 4
