"""Application configuration loaded from environment variables and .env."""

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class JobQueueBackend(StrEnum):
    POSTGRES = "postgres"
    SERVICE_BUS = "service_bus"


class Settings(BaseSettings):
    # Application
    project_name: str = "Mars Petcare -- Projected Fines API"
    version: str = "0.1.0"
    environment: str = "development"  # "production" | "staging" | "development"
    docs_enabled: bool = True

    # Logging
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/mars"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    # Azure OpenAI
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment_name: str = ""
    azure_openai_timeout_seconds: float = 90.0
    azure_openai_max_attempts: int = 3

    # Daily projection+summary cadence
    penalty_business_timezone: str = "UTC"
    penalty_daily_run_time: str = "01:00"

    # Job queue backend. job_item remains the source of truth for work state
    # regardless of whether jobs are polled from Postgres or dispatched via
    # Service Bus.
    job_queue_backend: JobQueueBackend = JobQueueBackend.POSTGRES
    service_bus_namespace: str = ""  # e.g. "mars-fines.servicebus.windows.net"
    service_bus_queue_name: str = "fine-projection-jobs"
    service_bus_max_wait_seconds: int = 10

    # Worker concurrency controls concurrent Azure OpenAI work. Increase
    # database pool capacity accordingly to avoid workers blocking on sessions.
    job_queue_worker_concurrency: int = 5
    job_queue_batch_size: int = 5
    job_queue_max_attempts: int = 5
    job_queue_backoff_base_seconds: int = 30
    job_queue_backoff_cap_seconds: int = 1800
    job_queue_backoff_jitter_seconds: int = 30
    job_queue_visibility_timeout_seconds: int = 300
    job_queue_item_deadline_seconds: int = 600
    job_queue_poll_interval_seconds: int = 5
    job_queue_idle_poll_max_seconds: int = 30

    # Shared rate-limit backoff across concurrent LLM calls. Per-call SDK retries
    # are insufficient under fan-out because concurrent workers can otherwise
    # retry in synchronized waves.
    llm_rate_limit_backoff_seconds: int = 60

    # Process-local concurrency limit for on-demand LLM generation. This prevents
    # concurrent API-triggered jobs from exhausting DB connections or LLM quota.
    on_demand_max_concurrent_summaries: int = 3
    on_demand_summary_acquire_timeout_seconds: int = 30

    # Maximum age, in days, for reusing a stored summary when projection
    # outputs remain unchanged.
    summary_max_reuse_days: int = 7
    summary_reuse_enabled: bool = False

    # Recovery window for PENDING summary rows whose job item was never committed.
    # The nightly batch re-enqueues eligible rows within this window.
    summary_pending_sweep_days: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
