"""Execute one claimed job item.

Dispatch table only -- the actual per-task-type work lives in the two
domain modules this file imports from (`app/workers/penalty_projection.py`,
`app/workers/penalty_mitigation.py`, was `fine_projection.py`/
`fine_mitigation.py` -- `fine`/`fines` -> `penalty`/`penalties` rename, per
the approved plan's naming convention). This split keeps the two penalty
sub-domains' job-execution logic from being interleaved in one function,
which used to make it easy to change one domain's handling and miss the
other's.

`ClaimedJob.task_type` was renamed `item_type` (see `app.queue.types`'
module docstring -- it matches `process.job_item.item_type`, the real
discriminator column, now that the domain-shaped business key moved off
`ClaimedJob` entirely).
"""

from __future__ import annotations

from collections.abc import Callable

from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import Settings
from app.db.session import Database
from app.models.enums import JobTaskType
from app.queue.types import ClaimedJob
from app.workers.penalty_mitigation import run_mitigation_summary
from app.workers.penalty_projection import run_projection, run_summary


def execute_job(
    job: ClaimedJob,
    database: Database,
    # Part of the fixed handler contract (see loop.py's execute_job_fn seam) --
    # unused today, kept so a future settings-driven knob needs no signature change.
    settings: Settings,
    llm: AzureOpenAIChatClient,
    heartbeat: Callable[[], None] | None = None,
) -> None:
    """Execute one job; exception classification is handled by the worker loop.

    ORDER_RUN runs projection then summary. PROJECTION_SUMMARY_REGEN runs summary only
    and requires an existing projection. MITIGATION_SUMMARY_REGEN runs the
    mitigation summary only and requires already-persisted mitigation
    options (mitigation_option) -- mitigation options themselves are
    computed synchronously via the API, never through this job queue (see
    app/api/v1/penalties/mitigations.py).
    """
    if job.item_type == JobTaskType.ORDER_RUN:
        run_projection(job, database)
        run_summary(job, database, llm, heartbeat=heartbeat)
    elif job.item_type == JobTaskType.PROJECTION_SUMMARY_REGEN:
        run_summary(job, database, llm, heartbeat=heartbeat)
    elif job.item_type == JobTaskType.MITIGATION_SUMMARY_REGEN:
        run_mitigation_summary(job, database, llm, heartbeat=heartbeat)
    else:
        # Unknown task types are non-retryable.
        raise ValueError(f"Unknown item_type={job.item_type!r} for job_item_id={job.job_item_id}")
