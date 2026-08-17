"""Shared job and summary enums used across persistence, API, and worker layers.

JobItemStatus and SummaryStatus intentionally remain separate types even
where their values overlap. They represent different persistence domains:
job_item execution state and fine-summary state, respectively.
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
    ORDER_RUN = "ORDER_RUN"
    SUMMARY_REGEN = "SUMMARY_REGEN"


class JobRunType(StrEnum):
    SCHEDULED_DAILY = "SCHEDULED_DAILY"
    MANUAL_BATCH = "MANUAL_BATCH"
    ON_DEMAND = "ON_DEMAND"


class SummaryStatus(StrEnum):
    """Persistence state for a fine-summary record."""

    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"
    