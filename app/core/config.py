"""Application settings loaded from environment variables and .env."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application
    project_name: str = "Mars Petcare — CMIR Resolution System & Projected Fines"
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

    # CMIR: inbound email (Gmail IMAP)
    email_username: str = ""
    email_password: str = ""
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993
    email_search_subject: str = "CMIR"
    email_lookback_days: int = 1
    email_max_per_run: int = 10

    # CMIR: Azure Service Bus (mail-processing queue)
    servicebus_fully_qualified_namespace: str = "sb-mail-agent-dev.servicebus.windows.net"
    service_bus_queue_name: str = "mail-processing-queue"
    service_bus_session_id: str = "mail-processing"
    service_bus_max_wait_seconds: int = 30
    service_bus_lock_renew_seconds: int = 300
    service_bus_connection_string: str | None = None
    queue_enqueue_batch_limit: int = 25
    cmir_agent_api_base_url: str = "http://127.0.0.1:8000"
    cmir_agent_api_timeout_seconds: int = 30

    # Azure OpenAI -- used by both CMIR extraction and the fines fine-summary feature
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment_name: str = ""
    azure_openai_timeout_seconds: float = 90.0
    azure_openai_max_attempts: int = 3
    llm_temperature: float = 0.0

    # Daily projection+summary cadence (fines)
    penalty_business_timezone: str = "UTC"
    penalty_daily_run_time: str = "01:00"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


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
    api_version: str
    deployment: str
    temperature: float = 0.0

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMConfig:
        return cls(
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
            deployment=settings.azure_openai_deployment_name,
            temperature=settings.llm_temperature,
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
            fully_qualified_namespace=settings.servicebus_fully_qualified_namespace,
            queue_name=settings.service_bus_queue_name,
            session_id=settings.service_bus_session_id,
            max_wait_time_seconds=settings.service_bus_max_wait_seconds,
            lock_renew_seconds=settings.service_bus_lock_renew_seconds,
            enqueue_batch_limit=settings.queue_enqueue_batch_limit,
            agent_api_base_url=settings.cmir_agent_api_base_url,
            agent_api_timeout_seconds=settings.cmir_agent_api_timeout_seconds,
            connection_string=settings.service_bus_connection_string,
        )
