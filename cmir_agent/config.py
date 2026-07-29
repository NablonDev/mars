"""Application configuration, loaded once from environment variables.

Keeping this as small, typed, immutable dataclasses (rather than scattering
os.getenv() calls through the codebase) means every other module depends on
a stable config *shape*, not on the environment directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


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
    def from_env(cls) -> "EmailConfig":
        return cls(
            address=_require("EMAIL_USERNAME"),
            password=_require("EMAIL_PASSWORD"),
            imap_server=_require("IMAP_SERVER"),
            imap_port=int(os.getenv("IMAP_PORT", "993")),
        )


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    name: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        return cls(
            host=_require("DB_HOST"),
            port=int(os.getenv("DB_PORT", "5432")),
            name=_require("DB_NAME"),
            user=_require("DB_USER"),
            password=_require("DB_PASSWORD"),
        )


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    endpoint: str
    api_version: str
    deployment: str
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            api_key=_require("AZURE_OPENAI_API_KEY"),
            endpoint=_require("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            deployment=_require("AZURE_OPENAI_DEPLOYMENT_NAME"),
        )

    
@dataclass(frozen=True)
class AppConfig:
    email: EmailConfig
    database: DatabaseConfig
    llm: LLMConfig

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            email=EmailConfig.from_env(),
            database=DatabaseConfig.from_env(),
            llm=LLMConfig.from_env(),
        )
