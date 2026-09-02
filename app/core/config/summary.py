"""Penalty projection/mitigation summary settings.

Covers stored-summary reuse and the recovery-sweep window
(app.services.penalties._summary_base, app.workers.penalty_projection,
app.workers.penalty_mitigation), and the daily batch cadence's business
timezone.

`on_demand_max_concurrent_summaries`/`on_demand_acquire_timeout_seconds`
were removed here: `SummaryServiceBase.get_or_schedule` has no inline/
synchronous generation path left to bound the concurrency of -- it only
ever enqueues a `process.job_run`/`job_item` and returns PENDING (a
worker generates the summary later, out of band). Settings fields nothing
reads don't belong here; see the project's own `job_queue_max_attempts`
dead-config precedent.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SummarySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # Maximum age, in days, for reusing a stored summary when projection
    # outputs remain unchanged.
    max_reuse_days: int = Field(default=7, validation_alias="SUMMARY_MAX_REUSE_DAYS")
    reuse_enabled: bool = Field(default=False, validation_alias="SUMMARY_REUSE_ENABLED")

    # Recovery window for PENDING summary rows whose job item was never
    # committed. The nightly batch re-enqueues eligible rows within this window.
    pending_sweep_days: int = Field(default=3, validation_alias="SUMMARY_PENDING_SWEEP_DAYS")

    # Daily projection+summary cadence (penalties)
    business_timezone: str = Field(default="UTC", validation_alias="SUMMARY_BUSINESS_TIMEZONE")
    daily_run_time: str = Field(default="01:00", validation_alias="SUMMARY_DAILY_RUN_TIME")
