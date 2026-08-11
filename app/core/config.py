"""
Settings via pydantic-settings -- the one deliberate divergence from the
sibling cmir_agent project's plain-dataclass `config.py` (see
docs/FINE_ENGINE.md-adjacent notes in PROGRESS.local.md for why): this is the
FastAPI-ecosystem's own documented settings pattern, and was explicitly
asked for ("best standard way as per FastAPI").
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/mars"
    api_title: str = "Mars Petcare -- Projected Fines API"
    api_version: str = "0.1.0"

    # Root log level for the JSON stdout handler configured in
    # app/core/logging.py::configure_logging (wired up in app/main.py).
    log_level: str = "INFO"

    # SQLAlchemy connection pool sizing, applied to the one engine built
    # per process in app/main.py's `lifespan` (app/db/session.py::Database).
    # These are SQLAlchemy's own QueuePool defaults, made explicit and
    # tunable per environment rather than left implicit.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    # Azure OpenAI, for the LLM-powered projection-explanation feature
    # (app/agents/, app/services/explanation_service.py). Empty-string
    # defaults so the rest of the app (health check, unrelated tests)
    # doesn't break in environments without Azure credentials --
    # app.agents.providers.azure_openai.AzureOpenAIChatClient raises a clear
    # error at call time if these are unset, not at import time.
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment_name: str = ""

    # Per-call timeout and our own wrapper's retry budget
    # (app/agents/providers/azure_openai.py::AzureOpenAIChatClient).
    # Measured directly against a real deployment (bypassing this app's
    # code entirely -- a bare LangChain call, no tools, no retry wrapper):
    # latency for a realistic request ranged from ~2s to ~24s across
    # repeated calls with no relationship to payload size, tool binding,
    # or conversation-round shape -- this specific deployment's latency
    # is genuinely variable, not something client-side retry/backoff
    # tuning can smooth over. Retrying against variance doesn't fix
    # variance, it just multiplies the wait: at the old defaults
    # (3 attempts x 60s), one slow call could take 3+ minutes to
    # conclusively fail. Default is now 1 attempt (no retry) with a
    # timeout generous enough to cover the range actually observed --
    # raise azure_openai_max_attempts back up only if you have evidence
    # retrying actually recovers failures here (a truly transient
    # network blip), not as a default hedge against normal variance.
    azure_openai_timeout_seconds: float = 90.0
    azure_openai_max_attempts: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
