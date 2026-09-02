"""Repository for `cmir.email_action_log` -- append-only audit log for
email-level workflow events. Was `app/repositories/action_log.py`. Switched
to the project's standard injected-`Session` pattern, like the sibling
`cmir` repositories.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailActionLog


def _to_dict(row: EmailActionLog) -> dict:
    return {
        "id": row.id,
        "email_event_id": row.email_event_id,
        "action": row.action,
        "actor": row.actor,
        "details": row.details,
        "created_at": row.created_at,
    }


class ActionLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def log(self, email_event_id: UUID, action: str, actor: str, details: dict[str, Any]) -> dict:
        row = EmailActionLog(email_event_id=email_event_id, action=action, actor=actor, details=details)
        self._session.add(row)
        self._session.flush()
        return _to_dict(row)

    def list_for_email(self, email_event_id: UUID) -> list[dict]:
        rows = self._session.scalars(
            select(EmailActionLog)
            .where(EmailActionLog.email_event_id == email_event_id)
            .order_by(EmailActionLog.created_at.asc())
        ).all()
        return [_to_dict(r) for r in rows]
