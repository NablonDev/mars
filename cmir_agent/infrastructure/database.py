from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PGConnection

from cmir_agent.config import DatabaseConfig


class Database:
    """Thin factory around psycopg2 connections.

    Repositories depend on this abstraction rather than on psycopg2
    directly, so the connection strategy (single connection today, a pool
    later) can change in exactly one place.
    """

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config

    @contextmanager
    def connection(self) -> Iterator[PGConnection]:
        conn = psycopg2.connect(
            host=self._config.host,
            port=self._config.port,
            dbname=self._config.name,
            user=self._config.user,
            password=self._config.password,
        )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
