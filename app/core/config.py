"""Application settings loaded from environment variables and .env.

Every setting is a field on the single pydantic-settings ``Settings`` object
below -- the house convention (see CLAUDE.local.md). The small dataclasses
further down (``EmailConfig``, ``LLMConfig``, ``ServiceBusConfig``) are not a
second config-loading path: they're typed parameter objects the cmir services
were already written against, built from ``Settings`` via ``from_settings()``
in ``app/core/container.py`` rather than reading the environment themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class JobQueueBackend(StrEnum):
    POSTGRES = "postgres"
    SERVICE_BUS = "service_bus"


_INTERNAL_API_KEY_MIN_LENGTH = 64


class Settings(BaseSettings):
    # Application
    project_name: str = "Mars Petcare -- CMIR Resolution System & Projected Fines"
    version: str = "0.1.0"
    environment: str = "development"  # "production" | "staging" | "development"
    docs_enabled: bool = True

    # Security: shared-secret gate on every route except /health. No default --
    # a missing INTERNAL_API_KEY must fail app startup, never silently accept
    # unauthenticated requests.
    internal_api_key: str

    # Logging
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/mars"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    # CMIR: inbound email (Gmail IMAP)
    email_username: str = ""
    email_password: str = ""
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993
    email_search_subject: str = "CMIR"
    email_lookback_days: int = 1
    email_max_per_run: int = 10

    # CMIR: Azure Service Bus (mail-processing queue)
    service_bus_fully_qualified_namespace: str = "sb-mail-agent-dev.servicebus.windows.net"
    service_bus_queue_name: str = "mail-processing-queue"
    service_bus_session_id: str = "mail-processing"
    service_bus_max_wait_seconds: int = 30
    service_bus_lock_renew_seconds: int = 300
    service_bus_connection_string: str | None = None
    queue_enqueue_batch_limit: int = 25
    cmir_agent_api_base_url: str = "http://127.0.0.1:8000"
    cmir_agent_api_timeout_seconds: int = 30

    # Azure OpenAI -- used by both CMIR extraction and the fines fine-projection and mitigation summary
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment_name: str = ""
    azure_openai_timeout_seconds: float = 90.0
    azure_openai_max_attempts: int = 3
    llm_temperature: float = 0.0

    # Fines: batch job queue. job_item remains the source of truth for work
    # state regardless of whether jobs are polled from Postgres or dispatched
    # via Service Bus. Fields prefixed job_queue_service_bus_* (rather than
    # bare service_bus_*) to disambiguate from CMIR's own Service Bus queue
    # above -- two independent queues, same underlying Azure resource type.
    job_queue_backend: JobQueueBackend = JobQueueBackend.POSTGRES
    job_queue_service_bus_namespace: str = ""  # e.g. "mars-fines.servicebus.windows.net"
    job_queue_service_bus_queue_name: str = "fine-projection-jobs"
    job_queue_service_bus_max_wait_seconds: int = 10

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

    # Daily projection+summary cadence (fines)
    penalty_business_timezone: str = "UTC"
    penalty_daily_run_time: str = "01:00"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("internal_api_key")
    @classmethod
    def _internal_api_key_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("INTERNAL_API_KEY must not be blank")

        if len(value) < _INTERNAL_API_KEY_MIN_LENGTH:
            # Length, not entropy: 64 is the width of secrets.token_hex(32), the
            # generator .env.example documents. A token_urlsafe(32) key carries the
            # same 256 bits in 43 characters and is rejected here, so say what to
            # generate rather than leaving the reader to guess at the number.
            raise ValueError(
                f"INTERNAL_API_KEY must be at least {_INTERNAL_API_KEY_MIN_LENGTH} characters "
                '(generate with: python -c "import secrets; print(secrets.token_hex(32))")'
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@dataclass(frozen=True)
class EmailConfig:
    address: str
    password: str
    imap_server: str
    imap_port: int
    search_subject: str = "CMIR"
    lookback_days: int = 1
    max_per_run: int = 10

    @classmethod
    def from_settings(cls, settings: Settings) -> EmailConfig:
        return cls(
            address=settings.email_username,
            password=settings.email_password,
            imap_server=settings.imap_server,
            imap_port=settings.imap_port,
            search_subject=settings.email_search_subject,
            lookback_days=settings.email_lookback_days,
            max_per_run=settings.email_max_per_run,
        )


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    endpoint: str
    deployment: str
    temperature: float = 0.0
    timeout_seconds: float = 90.0
    max_retries: int = 3

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMConfig:
        return cls(
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            deployment=settings.azure_openai_deployment_name,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.azure_openai_timeout_seconds,
            max_retries=settings.azure_openai_max_attempts,
        )


@dataclass(frozen=True)
class ServiceBusConfig:
    fully_qualified_namespace: str
    queue_name: str
    session_id: str = "mail-processing"
    max_wait_time_seconds: int = 30
    lock_renew_seconds: int = 300
    enqueue_batch_limit: int = 25
    agent_api_base_url: str = "http://127.0.0.1:8000"
    agent_api_timeout_seconds: int = 30
    connection_string: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> ServiceBusConfig:
        return cls(
            fully_qualified_namespace=settings.service_bus_fully_qualified_namespace,
            queue_name=settings.service_bus_queue_name,
            session_id=settings.service_bus_session_id,
            max_wait_time_seconds=settings.service_bus_max_wait_seconds,
            lock_renew_seconds=settings.service_bus_lock_renew_seconds,
            enqueue_batch_limit=settings.queue_enqueue_batch_limit,
            agent_api_base_url=settings.cmir_agent_api_base_url,
            agent_api_timeout_seconds=settings.cmir_agent_api_timeout_seconds,
            connection_string=settings.service_bus_connection_string,
        )
