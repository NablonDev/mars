"""Application settings loaded from environment variables and .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
