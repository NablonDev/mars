"""Shared job and summary enums used across persistence, API, and worker layers.

JobItemStatus and SummaryStatus intentionally remain separate types even
where their values overlap. They represent different persistence domains:
job_item execution state, and the summary-record state shared by both
penalty projection and penalty mitigation summaries, respectively.
"""

from __future__ import annotations

from enum import StrEnum


class JobItemStatus(StrEnum):
    """Execution lifecycle for a job item."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEAD = "DEAD"


class JobTaskType(StrEnum):
    """`process.job_item.item_type` values -- shared by both the
    `cmir`/`po_validation` and `penalties` domains now that job_run/
    job_item live in `process`."""

    ORDER_RUN = "ORDER_RUN"
    PROJECTION_SUMMARY_REGEN = "PROJECTION_SUMMARY_REGEN"
    MITIGATION_SUMMARY_REGEN = "MITIGATION_SUMMARY_REGEN"
    # Computes and persists fresh mitigation options for a purchase order
    # against its already-persisted latest projection -- distinct from
    # MITIGATION_SUMMARY_REGEN (which only regenerates the LLM summary over
    # options that already exist). See app/workers/penalty_mitigation.py.
    MITIGATION_RUN = "MITIGATION_RUN"
    EMAIL_INGEST = "EMAIL_INGEST"
    PO_VALIDATION = "PO_VALIDATION"
    # Dispatched from `job_type=PENALTY_FULL_RUN_BATCH` -- one item per
    # matching purchase order, executing only its requested subset of
    # projection/projection_summary/mitigation/mitigation_summary steps
    # (stored in process.job_item.metadata) in that fixed dependency order.
    # See app/workers/penalty_full_run.py.
    PENALTY_FULL_RUN = "PENALTY_FULL_RUN"


class JobRunType(StrEnum):
    SCHEDULED_DAILY = "SCHEDULED_DAILY"
    MANUAL_BATCH = "MANUAL_BATCH"
    ON_DEMAND = "ON_DEMAND"


class SummaryStatus(StrEnum):
    """Persistence state shared by both the penalty projection summary and
    penalty mitigation summary records."""

    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class SummaryType(StrEnum):
    """`penalties.penalty_summary.summary_type` discriminator, replacing
    what were two separate tables (`projection_summary`/
    `mitigation_summary`)."""

    PROJECTION = "PROJECTION"
    MITIGATION = "MITIGATION"


class AgentDomain(StrEnum):
    """`process.agent.domain` values -- shared by both the `cmir`/
    `po_validation` and `penalties` domains now that the agent registry
    lives in `process`. Lowercase, matching every existing call site
    (`domain="cmir"` / `domain="penalties"`), unlike this module's other,
    uppercase enums."""

    CMIR = "cmir"
    PENALTIES = "penalties"
