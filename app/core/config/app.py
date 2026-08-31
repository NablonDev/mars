"""App-level identity, environment, logging, and the shared-secret auth gate.

Read by app.main (title/version/docs_url gating, log configuration) and
app.api.dependencies.require_internal_api_key (the X-Internal-Api-Key gate).
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INTERNAL_API_KEY_MIN_LENGTH = 64


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    project_name: str = Field(
        default="Mars Petcare -- CMIR Resolution System & Projected Penalties",
        validation_alias="APP_PROJECT_NAME",
    )
    version: str = Field(default="0.1.0", validation_alias="APP_VERSION")
    environment: str = Field(
        default="development",  # "production" | "staging" | "development"
        validation_alias="APP_ENVIRONMENT",
    )
    docs_enabled: bool = Field(default=True, validation_alias="APP_DOCS_ENABLED")
    log_level: str = Field(default="INFO", validation_alias="APP_LOG_LEVEL")

    # Security: shared-secret gate on every route except /health. No default --
    # a missing APP_INTERNAL_API_KEY must fail app startup, never silently
    # accept unauthenticated requests.
    internal_api_key: str = Field(validation_alias="APP_INTERNAL_API_KEY")

    @field_validator("internal_api_key")
    @classmethod
    def _internal_api_key_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("APP_INTERNAL_API_KEY must not be blank")

        if len(value) < _INTERNAL_API_KEY_MIN_LENGTH:
            # Length, not entropy: 64 is the width of secrets.token_hex(32), the
            # generator .env.example documents. A token_urlsafe(32) key carries the
            # same 256 bits in 43 characters and is rejected here, so say what to
            # generate rather than leaving the reader to guess at the number.
            raise ValueError(
                f"APP_INTERNAL_API_KEY must be at least {_INTERNAL_API_KEY_MIN_LENGTH} characters "
                '(generate with: python -c "import secrets; print(secrets.token_hex(32))")'
            )
        return value
