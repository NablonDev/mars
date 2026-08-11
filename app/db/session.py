"""
Database engine + session factory. One `Database` object owns the engine
and sessionmaker; repositories receive a `Session` via constructor
injection (see app/api/dependencies.py), not this class directly.

The engine is built once per process (app/main.py's `lifespan`) so its
connection pool is actually reused across requests; `dispose()` tears the
pool down on shutdown.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401  -- side-effect import, see docstring below
from app.db.base import FINES_SCHEMA, Base


def apply_sqlite_schema_translation(engine: Engine) -> Engine:
    """Translate the `fines` schema away on SQLite.

    `Base.metadata` is bound to the `fines` schema (app/db/base.py), which
    SQLite has no concept of. `schema_translate_map` is SQLAlchemy's own
    mechanism for exactly this case and applies to both DDL (`create_all`)
    and DML, so no caller -- tests included -- needs to know the schema
    exists. Non-SQLite engines are returned unchanged.

    Returns a new engine sharing the original's connection pool (that is
    what `Engine.execution_options()` does), so this is safe to wrap around
    a freshly created engine.
    """
    if engine.dialect.name == "sqlite":
        return engine.execution_options(schema_translate_map={FINES_SCHEMA: None})
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
        """Pool sizing is opt-in (`None` = leave SQLAlchemy's own default
        alone) and applied with `setdefault`, so a caller that supplies its
        own `poolclass` -- tests/conftest.py's SQLite `StaticPool`, which
        accepts none of these arguments -- is unaffected. Production values
        come from `Settings.db_pool_*` via app/main.py."""
        engine_kwargs.setdefault("future", True)
        engine_kwargs.setdefault("pool_pre_ping", True)
        if pool_size is not None:
            engine_kwargs.setdefault("pool_size", pool_size)
        if max_overflow is not None:
            engine_kwargs.setdefault("max_overflow", max_overflow)
        if pool_timeout is not None:
            engine_kwargs.setdefault("pool_timeout", pool_timeout)
        self._engine = apply_sqlite_schema_translation(create_engine(database_url, **engine_kwargs))
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False, expire_on_commit=False)

    @property
    def engine(self) -> Engine:
        return self._engine

    def create_all_tables(self) -> None:
        """Used by tests (SQLite) and local bootstrapping. Production
        schema changes go through Alembic (see alembic/), not this."""
        Base.metadata.create_all(self._engine)

    def dispose(self) -> None:
        """Close every pooled connection. An app-shutdown operation only
        (app/main.py's `lifespan`) -- never per request, which would defeat
        pooling entirely."""
        self._engine.dispose()

    def new_session(self) -> Session:
        """Raw session factory -- used by the FastAPI `get_db` dependency,
        which needs to control the request-scoped lifecycle itself
        (yield / commit / rollback / close) rather than via the
        contextmanager below."""
        return self._session_factory()

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Unit-of-work context manager for callers outside a FastAPI
        request (scripts, tests). `Generator`, not `Iterator` -- a
        `@contextmanager`-decorated function's return type should be
        annotated as the generator it actually is (Iterator only models
        __next__, not the send/throw machinery contextmanager relies on;
        annotating it as Iterator here is a deprecated pattern)."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
