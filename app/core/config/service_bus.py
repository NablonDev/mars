"""Azure Service Bus settings for CMIR's inbound mail-processing queue.

Read by app.queue.cmir_mail_producer.ServiceBusMailQueue via
ServiceBusConfig.from_settings(). Independent of the batch job queue's own
Service Bus backend -- see job_queue.py's service_bus_* fields, which
point at a separate Azure Service Bus resource of the same type.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceBusSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    namespace: str = Field(
        default="sb-mail-agent-dev.servicebus.windows.net",
        validation_alias="SERVICE_BUS_NAMESPACE",
    )
    connection_string: str | None = Field(default=None, validation_alias="SERVICE_BUS_CONNECTION_STRING")
    queue_name: str = Field(default="mail-processing-queue", validation_alias="SERVICE_BUS_QUEUE_NAME")
    session_id: str = Field(default="mail-processing", validation_alias="SERVICE_BUS_SESSION_ID")
    max_wait_seconds: int = Field(default=30, validation_alias="SERVICE_BUS_MAX_WAIT_SECONDS")
    lock_renew_seconds: int = Field(default=300, validation_alias="SERVICE_BUS_LOCK_RENEW_SECONDS")
    enqueue_batch_limit: int = Field(default=25, validation_alias="SERVICE_BUS_ENQUEUE_BATCH_LIMIT")
    agent_api_base_url: str = Field(
        default="http://127.0.0.1:8000", validation_alias="SERVICE_BUS_AGENT_API_BASE_URL"
    )
    agent_api_timeout_seconds: int = Field(
        default=30, validation_alias="SERVICE_BUS_AGENT_API_TIMEOUT_SECONDS"
    )
