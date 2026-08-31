"""Repository-layer tests for the shared batch job queue
(`process.job_run`/`process.job_item`), run against in-memory SQLite (see
conftest.py). Was tests/unit/repositories/test_job_queue_repository.py
against the old order_id/projection_date/task_type-keyed JobItem --
relocated onto the generic item_type/dedupe_key shape (see
app/repositories/process/job_queue.py's module docstring). Covers
row-shape, filter, and state-machine logic; the concurrency guarantees the
Postgres-only claim/enqueue paths actually provide are proven separately
against a real Postgres in tests/integration/test_job_queue_postgres.py.

The stranded-pending-summary recovery-sweep tests that used to live here
moved to test_penalty_summary_repository.py, alongside
`PenaltySummaryRepository.find_stranded_pending` (see that repository
module's docstring for why).
"""

from datetime import timedelta

from app.repositories.process.job_queue import JobQueueRepository, _utcnow


def _make_run(repo: JobQueueRepository):
    return repo.create_run(job_type="PENALTY_PROJECTION_BATCH", trigger_type="MANUAL_BATCH")


def test_enqueue_is_idempotent_for_the_same_dedupe_key(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    first = repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-1:2026-08-13", max_attempts=5)
    second = repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-1:2026-08-13", max_attempts=5)

    assert first is not None
    assert second is not None
    assert first["id"] == second["id"]

    items = repo.list_run_items(run["id"])
    assert len(items) == 1


def test_enqueue_without_dedupe_key_never_dedupes(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key=None, max_attempts=5)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key=None, max_attempts=5)

    items = repo.list_run_items(run["id"])
    assert len(items) == 2


def test_enqueue_many_is_idempotent_and_bulk(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    # ORD-1 enqueued individually first...
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-1:2026-08-13", max_attempts=5)

    inserted = repo.enqueue_many(
        run["id"],
        [
            {"item_type": "ORDER_RUN", "dedupe_key": "ORD-1:2026-08-13"},  # dup
            {"item_type": "ORDER_RUN", "dedupe_key": "ORD-2:2026-08-13"},
            {"item_type": "ORDER_RUN", "dedupe_key": "ORD-3:2026-08-13"},
            {"item_type": "ORDER_RUN", "dedupe_key": "ORD-3:2026-08-13"},  # dup w/in batch
        ],
        max_attempts=5,
    )

    assert inserted == 2
    items = repo.list_run_items(run["id"])
    assert len(items) == 3


def test_claim_batch_excludes_future_available_at(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-FUTURE", max_attempts=5)

    # push the row's availability into the future directly
    item = repo.list_run_items(run["id"])[0]
    from app.models import JobItem

    row = db_session.get(JobItem, item["id"])
    row.available_at = _utcnow() + timedelta(hours=1)
    db_session.flush()

    claimed = repo.claim_batch("worker-1", limit=10)
    assert claimed == []


def test_claim_batch_excludes_terminal_rows(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-A", max_attempts=5)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-B", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)
    assert len(claimed) == 2

    repo.mark_succeeded(claimed[0]["id"], "worker-1")
    repo.mark_dead(claimed[1]["id"], "worker-1", "boom", "FATAL")

    second_claim = repo.claim_batch("worker-2", limit=10)
    assert second_claim == []


def test_claim_batch_increments_attempt_count(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-A", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)
    assert len(claimed) == 1
    assert claimed[0]["attempt_count"] == 1
    assert claimed[0]["status"] == "RUNNING"
    assert claimed[0]["locked_by"] == "worker-1"


def test_mark_failed_retries_then_goes_dead(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    enqueued = repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-A", max_attempts=5)

    from app.models import JobItem

    row = db_session.get(JobItem, enqueued["id"])
    row.max_attempts = 2
    db_session.flush()

    # attempt 1: claim, fail -> back to PENDING
    claimed = repo.claim_batch("worker-1", limit=10)[0]
    assert claimed["attempt_count"] == 1
    result = repo.mark_failed(claimed["id"], "worker-1", "transient", "TIMEOUT", retry_in_seconds=0)
    assert result["status"] == "PENDING"
    assert result["locked_by"] is None

    # attempt 2: claim again, fail -> now DEAD (attempt_count == max_attempts)
    claimed_again = repo.claim_batch("worker-1", limit=10)
    assert len(claimed_again) == 1
    assert claimed_again[0]["attempt_count"] == 2
    result = repo.mark_failed(claimed_again[0]["id"], "worker-1", "transient", "TIMEOUT", retry_in_seconds=0)
    assert result["status"] == "DEAD"


def test_mark_dead_is_terminal_immediately(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-A", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)[0]
    assert claimed["attempt_count"] == 1  # nowhere near max_attempts (default 5)

    result = repo.mark_dead(claimed["id"], "worker-1", "non-retryable", "BAD_INPUT")
    assert result["status"] == "DEAD"


def test_heartbeat_returns_false_after_ownership_loss(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-A", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)[0]
    assert repo.heartbeat(claimed["id"], "worker-1") is True

    # simulate reclaim_stale (or another worker) taking ownership away
    from app.models import JobItem

    row = db_session.get(JobItem, claimed["id"])
    row.locked_by = "worker-2"
    db_session.flush()

    assert repo.heartbeat(claimed["id"], "worker-1") is False


def test_release_does_not_consume_an_attempt(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-A", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)[0]
    assert claimed["attempt_count"] == 1

    released = repo.release(claimed["id"], "worker-1")
    assert released["status"] == "PENDING"
    assert released["locked_by"] is None
    assert released["attempt_count"] == 1  # unchanged by release itself

    reclaimed = repo.claim_batch("worker-2", limit=10)[0]
    assert reclaimed["attempt_count"] == 2  # only the claim itself increments


def test_reclaim_stale_resets_stale_running_row_and_leaves_fresh_one_alone(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-STALE", max_attempts=5)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-FRESH", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)
    stale_id = next(c["id"] for c in claimed if c["dedupe_key"] == "ORD-STALE")
    fresh_id = next(c["id"] for c in claimed if c["dedupe_key"] == "ORD-FRESH")

    from app.models import JobItem

    stale_row = db_session.get(JobItem, stale_id)
    stale_row.heartbeat_at = _utcnow() - timedelta(minutes=10)
    db_session.flush()

    reset_count = repo.reclaim_stale(visibility_timeout_seconds=60)
    assert reset_count == 1

    stale_after = db_session.get(JobItem, stale_id)
    fresh_after = db_session.get(JobItem, fresh_id)
    assert stale_after.status == "PENDING"
    assert stale_after.locked_by is None
    assert fresh_after.status == "RUNNING"


def test_reclaim_stale_also_resets_running_rows_with_no_heartbeat_ever_recorded(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-A", max_attempts=5)
    claimed = repo.claim_batch("worker-1", limit=10)[0]

    from app.models import JobItem

    row = db_session.get(JobItem, claimed["id"])
    row.heartbeat_at = None
    row.locked_at = _utcnow() - timedelta(minutes=10)
    db_session.flush()

    reset_count = repo.reclaim_stale(visibility_timeout_seconds=60)
    assert reset_count == 1
    assert db_session.get(JobItem, claimed["id"]).status == "PENDING"


def test_get_run_summary_counts_correctly(db_session):
    repo = JobQueueRepository(db_session)
    run = repo.create_run(
        job_type="PENALTY_PROJECTION_BATCH", trigger_type="MANUAL_BATCH", requested_item_count=4
    )
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-A", max_attempts=5)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-B", max_attempts=5)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-C", max_attempts=5)
    repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-D", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)
    repo.mark_succeeded(claimed[0]["id"], "worker-1")
    repo.mark_dead(claimed[1]["id"], "worker-1", "boom", "FATAL")
    # claimed[2] stays RUNNING, claimed[3] stays RUNNING

    summary = repo.get_run_summary(run["id"])
    assert summary["requested_item_count"] == 4
    assert summary["counts"]["SUCCEEDED"] == 1
    assert summary["counts"]["DEAD"] == 1
    assert summary["counts"]["RUNNING"] == 2
    assert summary["counts"]["PENDING"] == 0
    assert summary["total_items"] == 4


def test_enqueue_persists_the_passed_max_attempts(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    item = repo.enqueue(run["id"], "ORDER_RUN", dedupe_key="ORD-MAX-ATTEMPTS", max_attempts=10)

    assert item is not None
    assert item["max_attempts"] == 10


def test_enqueue_many_persists_the_passed_max_attempts(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    repo.enqueue_many(
        run["id"],
        [{"item_type": "ORDER_RUN", "dedupe_key": "ORD-MAX-ATTEMPTS-BULK"}],
        max_attempts=10,
    )

    items = repo.list_run_items(run["id"])
    assert items[0]["max_attempts"] == 10
