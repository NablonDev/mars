"""Postgres connection-pool settings.

Read by app.main's lifespan and app.core.container.Container.build() to
construct the process-wide Database (app.db.session.Database).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # Historical flat name, restored -- DATABASE_URL predates the nested
    # __ delimiter scheme and has no other historical spelling.
    url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/mars",
        validation_alias="DATABASE_URL",
    )
    pool_size: int = Field(default=5, validation_alias="DATABASE_POOL_SIZE")
    max_overflow: int = Field(default=10, validation_alias="DATABASE_MAX_OVERFLOW")
    pool_timeout: int = Field(default=30, validation_alias="DATABASE_POOL_TIMEOUT")
