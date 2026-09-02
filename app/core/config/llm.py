"""Azure OpenAI settings, shared by CMIR extraction and the penalties
projection/mitigation summary agents.

Read by app.agents.providers.azure_openai.AzureOpenAIChatClient and
app.services.cmir.extractor.AzureOpenAICMIRExtractor via LLMConfig.from_settings(),
and by app.workers.loop / app.api.dependencies for the shared 429 backoff gate.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AzureOpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # Historical flat names, restored (confirmed via git log pre-redesign) --
    # AZURE_OPENAI_* predates the nested __ delimiter scheme. There is no
    # AZURE_OPENAI_API_VERSION field: the v1 GA API dropped the dated
    # api-version param entirely, so no field/alias exists for it here.
    api_key: str = Field(default="", validation_alias="AZURE_OPENAI_API_KEY")
    endpoint: str = Field(default="", validation_alias="AZURE_OPENAI_ENDPOINT")
    deployment: str = Field(default="", validation_alias="AZURE_OPENAI_DEPLOYMENT_NAME")

    # No historical precedent for these -- AZURE_OPENAI_<FIELD_NAME> for consistency.
    timeout_seconds: float = Field(default=90.0, validation_alias="AZURE_OPENAI_TIMEOUT_SECONDS")
    max_attempts: int = Field(default=3, validation_alias="AZURE_OPENAI_MAX_ATTEMPTS")
    temperature: float = Field(default=0.0, validation_alias="AZURE_OPENAI_TEMPERATURE")

    # Shared rate-limit backoff across concurrent LLM calls. Per-call SDK retries
    # are insufficient under fan-out because concurrent workers can otherwise
    # retry in synchronized waves.
    rate_limit_backoff_seconds: int = Field(
        default=60, validation_alias="AZURE_OPENAI_RATE_LIMIT_BACKOFF_SECONDS"
    )
