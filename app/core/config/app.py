"""App identity, environment, logging, and authentication configuration."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INTERNAL_API_KEY_MIN_LENGTH = 64


class AppSettings(BaseSettings):
    """Application identity, environment, logging level, and internal-API authentication."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    project_name: str = Field(
        default=(
            "Mars Petcare Backend: CMIR Email Resolution, PO Validation, "
            "and Projected Penalties, Mitigation & Dispute Resolution"
        ),
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
        """Reject a blank or too-short APP_INTERNAL_API_KEY so a misconfigured app fails at startup."""
        if not value.strip():
            raise ValueError("APP_INTERNAL_API_KEY must not be blank")

        if len(value) < _INTERNAL_API_KEY_MIN_LENGTH:
            # Enforce minimum length to match secrets.token_hex(32).
            raise ValueError(
                f"APP_INTERNAL_API_KEY must be at least {_INTERNAL_API_KEY_MIN_LENGTH} characters "
                '(generate with: python -c "import secrets; print(secrets.token_hex(32))")'
            )
        return value
