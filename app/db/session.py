"""SQLAlchemy engine, connection pool, and session management."""

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import CMIR_SCHEMA, COMMON_SCHEMA, PENALTIES_SCHEMA, PROCESS_SCHEMA, Base


def apply_sqlite_schema_translation(engine: Engine) -> Engine:
    """Translate the application schema away for SQLite.

    LANGGRAPH_SCHEMA is deliberately not included here -- no ORM model is
    bound to it (LangGraph's own PostgresSaver populates it at runtime),
    so there is nothing on Base.metadata that would need translating.
    """
    if engine.dialect.name == "sqlite":
        return engine.execution_options(
            schema_translate_map={
                COMMON_SCHEMA: None,
                PROCESS_SCHEMA: None,
                CMIR_SCHEMA: None,
                PENALTIES_SCHEMA: None,
            },
        )
    return engine


def checkpoint_dsn(database_url: str, schema: str) -> str:
    """Convert a SQLAlchemy PostgreSQL URL to a psycopg DSN.

    LangGraph's ``PostgresSaver`` connects with psycopg directly and doesn't
    understand SQLAlchemy's ``+psycopg``/``+psycopg2`` driver suffix.

    The PostgreSQL ``search_path`` is configured on the resulting DSN so
    LangGraph's checkpoint tables are created in the specified schema
    without changing the database-level configuration.
    """
    parsed = urlsplit(database_url)
    scheme = parsed.scheme.replace("+psycopg2", "").replace("+psycopg", "")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("options", f"-csearch_path={schema},public"))

    return urlunsplit((scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


class Database:
    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int | None = None,
        max_overflow: int | None = None,
        pool_timeout: int | None = None,
        **engine_kwargs: Any,
    ) -> None:
        engine_kwargs.setdefault("future", True)
        engine_kwargs.setdefault("pool_pre_ping", True)
        # UUID primary keys (agent_runs.id, email_events.id, ...) flow into JSON/JSONB
        # columns (agent_traces.input_snapshot, pending_human_actions.payload, ...) as
        # raw graph state -- stock json.dumps can't encode a uuid.UUID, so fall back to
        # str() for it (and anything else it can't natively encode) at the engine level,
        # covering every JSON/JSONB column through this one Database instance.
        engine_kwargs.setdefault("json_serializer", lambda obj: json.dumps(obj, default=str))

        if pool_size is not None:
            engine_kwargs.setdefault("pool_size", pool_size)

        if max_overflow is not None:
            engine_kwargs.setdefault("max_overflow", max_overflow)

        if pool_timeout is not None:
            engine_kwargs.setdefault("pool_timeout", pool_timeout)

        self._engine = apply_sqlite_schema_translation(create_engine(database_url, **engine_kwargs))
        self._session_factory = sessionmaker(
            bind=self._engine,
            autoflush=False,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    def create_all_tables(self) -> None:
        """Create ORM tables for tests and local bootstrapping."""
        Base.metadata.create_all(self._engine)

    def dispose(self) -> None:
        """Dispose the engine and its connection pool."""
        self._engine.dispose()

    def new_session(self) -> Session:
        """Create a session for a caller-managed lifecycle."""
        return self._session_factory()

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Provide a transactional session for non-request callers."""
        session = self._session_factory()

        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
