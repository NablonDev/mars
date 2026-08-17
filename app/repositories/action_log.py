from __future__ import annotations

from typing import Any

from app.db.session import Database
from app.models.email import EmailActionLogORM


class PostgresActionLogRepository:
    """SQLAlchemy repository for email-level action audit events."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def log(self, email_id: Any, action: str, actor: str, details: dict[str, Any]) -> None:
        with self._db.session() as session:
            session.add(
                EmailActionLogORM(
                    email_id=email_id,
                    action=action,
                    actor=actor,
                    details=details,
                )
            )
