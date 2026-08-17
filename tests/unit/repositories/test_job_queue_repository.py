"""Repository-layer tests for the batch job queue, run against in-memory
SQLite (see conftest.py). Covers row-shape, filter, and state-machine
logic; the concurrency guarantees the Postgres-only claim/enqueue paths
actually provide are proven separately against a real Postgres in
tests/integration/test_job_queue_postgres.py.
"""

from datetime import date, timedelta
from uuid import uuid4

from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.job_queue import JobQueueRepository, _utcnow


def _make_run(repo: JobQueueRepository, projection_date: date = date(2026, 8, 13)):
    return repo.create_run(run_type="MANUAL_BATCH", projection_date=projection_date)


def test_enqueue_is_idempotent_for_the_same_key(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    first = repo.enqueue(run["id"], "ORD-1", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
    second = repo.enqueue(run["id"], "ORD-1", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

    assert first is not None
    assert second is not None
    assert first["id"] == second["id"]

    items = repo.list_run_items(run["id"])
    assert len(items) == 1


def test_enqueue_many_is_idempotent_and_bulk(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    # ORD-1 enqueued individually first...
    repo.enqueue(run["id"], "ORD-1", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

    inserted = repo.enqueue_many(
        run["id"],
        [
            {"order_id": "ORD-1", "projection_date": date(2026, 8, 13), "task_type": "ORDER_RUN"},  # dup
            {"order_id": "ORD-2", "projection_date": date(2026, 8, 13), "task_type": "ORDER_RUN"},
            {"order_id": "ORD-3", "projection_date": date(2026, 8, 13), "task_type": "ORDER_RUN"},
            {
                "order_id": "ORD-3",
                "projection_date": date(2026, 8, 13),
                "task_type": "ORDER_RUN",
            },  # dup w/in batch
        ],
        max_attempts=5,
    )

    assert inserted == 2
    items = repo.list_run_items(run["id"])
    assert len(items) == 3


def test_claim_batch_excludes_future_available_at(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORD-FUTURE", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

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
    repo.enqueue(run["id"], "ORD-A", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
    repo.enqueue(run["id"], "ORD-B", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)
    assert len(claimed) == 2

    repo.mark_succeeded(claimed[0]["id"], "worker-1")
    repo.mark_dead(claimed[1]["id"], "worker-1", "boom", "FATAL")

    second_claim = repo.claim_batch("worker-2", limit=10)
    assert second_claim == []


def test_claim_batch_increments_attempt_count(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORD-A", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)
    assert len(claimed) == 1
    assert claimed[0]["attempt_count"] == 1
    assert claimed[0]["status"] == "RUNNING"
    assert claimed[0]["locked_by"] == "worker-1"


def test_mark_failed_retries_then_goes_dead(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    enqueued = repo.enqueue(run["id"], "ORD-A", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

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
    repo.enqueue(run["id"], "ORD-A", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)[0]
    assert claimed["attempt_count"] == 1  # nowhere near max_attempts (default 5)

    result = repo.mark_dead(claimed["id"], "worker-1", "non-retryable", "BAD_INPUT")
    assert result["status"] == "DEAD"


def test_heartbeat_returns_false_after_ownership_loss(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)
    repo.enqueue(run["id"], "ORD-A", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

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
    repo.enqueue(run["id"], "ORD-A", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

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
    repo.enqueue(run["id"], "ORD-STALE", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
    repo.enqueue(run["id"], "ORD-FRESH", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

    claimed = repo.claim_batch("worker-1", limit=10)
    stale_id = next(c["id"] for c in claimed if c["order_id"] == "ORD-STALE")
    fresh_id = next(c["id"] for c in claimed if c["order_id"] == "ORD-FRESH")

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
    repo.enqueue(run["id"], "ORD-A", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
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
    run = repo.create_run(run_type="MANUAL_BATCH", projection_date=date(2026, 8, 13), requested_item_count=4)
    repo.enqueue(run["id"], "ORD-A", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
    repo.enqueue(run["id"], "ORD-B", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
    repo.enqueue(run["id"], "ORD-C", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
    repo.enqueue(run["id"], "ORD-D", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)

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


# ---------------------------------------------------------------------
# find_stranded_pending_summaries (recovery sweep, see app.workers.sweep)
# ---------------------------------------------------------------------

_AGENT_ID = uuid4()
_PROMPT_VERSION = "v-test"


def _make_pending_summary(db_session, order_id: str, as_of_date: date) -> None:
    FineSummaryRepository(db_session).create_pending(
        order_id=order_id,
        as_of_date=as_of_date,
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        context_hash="deadbeef",
    )
    db_session.commit()


def test_find_stranded_pending_summaries_finds_a_row_with_no_job_item(db_session):
    _make_pending_summary(db_session, "ORD-STRANDED", date(2026, 8, 13))

    repo = JobQueueRepository(db_session)
    stranded = repo.find_stranded_pending_summaries(date(2026, 8, 10), date(2026, 8, 14))

    assert stranded == [{"order_id": "ORD-STRANDED", "as_of_date": date(2026, 8, 13)}]


def test_find_stranded_pending_summaries_excludes_a_row_with_a_job_item(db_session):
    _make_pending_summary(db_session, "ORD-COVERED", date(2026, 8, 13))

    repo = JobQueueRepository(db_session)
    run = repo.create_run(run_type="ON_DEMAND", projection_date=date(2026, 8, 13))
    repo.enqueue(run["id"], "ORD-COVERED", date(2026, 8, 13), "SUMMARY_REGEN", max_attempts=5)
    db_session.commit()

    stranded = repo.find_stranded_pending_summaries(date(2026, 8, 10), date(2026, 8, 14))

    assert stranded == []


def test_find_stranded_pending_summaries_excludes_a_row_whose_job_item_is_terminal(db_session):
    _make_pending_summary(db_session, "ORD-DEAD-COVERED", date(2026, 8, 13))

    repo = JobQueueRepository(db_session)
    run = repo.create_run(run_type="ON_DEMAND", projection_date=date(2026, 8, 13))
    item = repo.enqueue(run["id"], "ORD-DEAD-COVERED", date(2026, 8, 13), "SUMMARY_REGEN", max_attempts=5)
    repo.claim_batch("worker-1", limit=1, job_item_ids=[item["id"]])
    repo.mark_dead(item["id"], "worker-1", "boom", "SOME_FAILURE")
    db_session.commit()

    stranded = repo.find_stranded_pending_summaries(date(2026, 8, 10), date(2026, 8, 14))

    assert stranded == []


def test_find_stranded_pending_summaries_excludes_a_row_outside_the_date_window(db_session):
    _make_pending_summary(db_session, "ORD-TOO-OLD", date(2026, 8, 1))

    repo = JobQueueRepository(db_session)
    stranded = repo.find_stranded_pending_summaries(date(2026, 8, 10), date(2026, 8, 14))

    assert stranded == []


def test_find_stranded_pending_summaries_excludes_ready_and_failed_rows(db_session):
    repo = JobQueueRepository(db_session)
    summaries = FineSummaryRepository(db_session)

    summaries.create_pending(
        order_id="ORD-READY",
        as_of_date=date(2026, 8, 13),
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        context_hash="hash1",
    )
    summaries.mark_ready(
        order_id="ORD-READY",
        as_of_date=date(2026, 8, 13),
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        model_name="gpt-test",
        summary="All clear.",
    )
    summaries.create_pending(
        order_id="ORD-FAILED",
        as_of_date=date(2026, 8, 13),
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        context_hash="hash2",
    )
    summaries.mark_failed(
        order_id="ORD-FAILED",
        as_of_date=date(2026, 8, 13),
        agent_id=_AGENT_ID,
        prompt_version=_PROMPT_VERSION,
        error_message="boom",
    )
    db_session.commit()

    stranded = repo.find_stranded_pending_summaries(date(2026, 8, 10), date(2026, 8, 14))

    assert stranded == []


def test_enqueue_persists_the_passed_max_attempts(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    item = repo.enqueue(run["id"], "ORD-MAX-ATTEMPTS", date(2026, 8, 13), "ORDER_RUN", max_attempts=10)

    assert item is not None
    assert item["max_attempts"] == 10


def test_enqueue_many_persists_the_passed_max_attempts(db_session):
    repo = JobQueueRepository(db_session)
    run = _make_run(repo)

    repo.enqueue_many(
        run["id"],
        [
            {
                "order_id": "ORD-MAX-ATTEMPTS-BULK",
                "projection_date": date(2026, 8, 13),
                "task_type": "ORDER_RUN",
            }
        ],
        max_attempts=10,
    )

    items = repo.list_run_items(run["id"])
    assert items[0]["max_attempts"] == 10


def test_find_stranded_pending_summaries_excludes_a_row_covered_by_an_order_run(db_session):
    """An ORDER_RUN item produces the summary for the same (order, date), so
    the ledger row is NOT stranded -- even though no SUMMARY_REGEN exists.

    Matching the anti-join on task_type='SUMMARY_REGEN' would report this
    row as stranded and let the sweep enqueue a second item alongside the
    live ORDER_RUN. Both would then generate the same narrative, and
    nothing at the DB level would stop it: uq_job_item_inflight is scoped
    per task_type, so the two do not collide. Correct data, double the
    LLM spend.
    """
    _make_pending_summary(db_session, "ORD-BATCH-COVERED", date(2026, 8, 13))

    repo = JobQueueRepository(db_session)
    run = repo.create_run(run_type="SCHEDULED_DAILY", projection_date=date(2026, 8, 13))
    repo.enqueue(run["id"], "ORD-BATCH-COVERED", date(2026, 8, 13), "ORDER_RUN", max_attempts=5)
    db_session.commit()

    stranded = repo.find_stranded_pending_summaries(date(2026, 8, 10), date(2026, 8, 14))

    assert stranded == []
