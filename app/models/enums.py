"""Shared job and summary enums used across persistence, API, and worker layers.

JobItemStatus and SummaryStatus intentionally remain separate types even
where their values overlap. They represent different persistence domains:
job_item execution state, and the summary-record state shared by both
fine projection and fine mitigation summaries, respectively.
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
    PROJECTION_SUMMARY_REGEN = "PROJECTION_SUMMARY_REGEN"
    MITIGATION_SUMMARY_REGEN = "MITIGATION_SUMMARY_REGEN"


class JobRunType(StrEnum):
    SCHEDULED_DAILY = "SCHEDULED_DAILY"
    MANUAL_BATCH = "MANUAL_BATCH"
    ON_DEMAND = "ON_DEMAND"


class SummaryStatus(StrEnum):
    """Persistence state shared by both the fine projection summary and
    fine mitigation summary records."""

    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"
