"""SQLAlchemy engine, connection pool, and session management."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import CMIR_SCHEMA, FINES_SCHEMA, Base


def apply_sqlite_schema_translation(engine: Engine) -> Engine:
    """Translate the application schema away for SQLite."""
    if engine.dialect.name == "sqlite":
        return engine.execution_options(
            schema_translate_map={
                CMIR_SCHEMA: None,
                FINES_SCHEMA: None,
            },
        )
    return engine


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
