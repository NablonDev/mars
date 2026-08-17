from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DatabaseConfig


class Database:
    """SQLAlchemy engine/session factory for repository adapters."""

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._engine = create_engine(self._url(config), pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine,
            autoflush=False,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> Engine:
        """Return the SQLAlchemy engine for migrations or diagnostics."""
        return self._engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Provide one transactional SQLAlchemy session."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _url(config: DatabaseConfig) -> str:
        return config.sqlalchemy_url()
