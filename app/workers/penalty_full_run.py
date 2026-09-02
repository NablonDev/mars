"""Combined-run job execution for `JobTaskType.PENALTY_FULL_RUN`.

Dispatched from `job_type=PENALTY_FULL_RUN_BATCH`
(`app/api/v1/job_runs.py::_trigger_penalty_full_run_batch`) -- one
`process.job_item` per matching purchase order, carrying the requested
`steps` (any subset/order of `"projection"`/`"projection_summary"`/
`"mitigation"`/`"mitigation_summary"`) on `process.job_item.metadata`
rather than a new `penalty_job_item_context` column, since that JSONB
column already exists and needs no migration.

`run_full_run` below adds no new domain logic of its own: it only reads
back `steps` and sequences the four step functions that already exist
(`app.workers.penalty_projection.run_projection`/`run_summary`,
`app.workers.penalty_mitigation.run_mitigation`/`run_mitigation_summary`),
always in that fixed dependency order regardless of the order `steps` was
submitted in -- mirroring `ORDER_RUN`'s existing precedent in
`app/workers/dispatch.py`, which already chains `run_projection()` then
`run_summary()` back to back for one item.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.db.session import Database
from app.queue.types import ClaimedJob
from app.repositories.process.job_queue import JobQueueRepository
from app.workers.penalty_mitigation import run_mitigation, run_mitigation_summary
from app.workers.penalty_projection import run_projection, run_summary


def _missing_steps_error(job_item_id: UUID) -> ValueError:
    """Non-retryable: `execute_job`/`classify_failure` treat a bare
    `ValueError` (not an `AppError`) as DEAD_LETTER -- a job item with no
    `steps` recorded in its metadata can never succeed on retry."""
    return ValueError(
        f"No `steps` found in process.job_item.metadata for job_item_id={job_item_id!r} -- "
        "cannot execute PENALTY_FULL_RUN without knowing which steps to run."
    )


def run_full_run(
    job: ClaimedJob,
    database: Database,
    llm: AzureOpenAIChatClient,
    *,
    heartbeat: Callable[[], None] | None,
) -> None:
    """Execute only the `steps` requested for this job item, in the fixed
    dependency order: projection -> projection_summary -> mitigation ->
    mitigation_summary.

    Each underlying step function opens (and commits/closes) its own DB
    session, exactly as `dispatch.execute_job`'s `ORDER_RUN` branch already
    does when it calls `run_projection` then `run_summary` back to back --
    this function is only the sequencing layer on top of that same
    precedent, extended to all four steps.
    """
    with database.session() as session:
        item = JobQueueRepository(session).get_item(job.job_item_id)

    if item is None:
        raise _missing_steps_error(job.job_item_id)

    steps: set[str] = set(item["metadata_json"].get("steps") or [])
    if not steps:
        raise _missing_steps_error(job.job_item_id)

    if "projection" in steps:
        run_projection(job, database)
    if "projection_summary" in steps:
        run_summary(job, database, llm, heartbeat=heartbeat)
    if "mitigation" in steps:
        run_mitigation(job, database)
    if "mitigation_summary" in steps:
        run_mitigation_summary(job, database, llm, heartbeat=heartbeat)
