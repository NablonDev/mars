from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, or_, select, update

from app.db.session import Database
from app.models.email import EmailEventORM
from app.schemas.cmir import CMIR, EmailMessage

logger = logging.getLogger(__name__)


class PostgresEmailRepository:
    """SQLAlchemy repository for persisted inbound emails."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def save(self, email: EmailMessage) -> Any:
        with self._db.session() as session:
            record = EmailEventORM(
                sender=email.sender,
                subject=email.subject,
                raw_content=email.body,
                source_message_id=email.source_message_id,
                source_imap_id=email.imap_id,
                queue_status="processed",
                processed_at=func.now(),
            )
            session.add(record)
            session.flush()
            return record.id

    def save_for_queue(self, email: EmailMessage) -> dict[str, Any]:
        with self._db.session() as session:
            existing = None
            filters = []
            if email.source_message_id:
                filters.append(EmailEventORM.source_message_id == email.source_message_id)
            if email.imap_id:
                filters.append(EmailEventORM.source_imap_id == email.imap_id)
            if filters:
                existing = session.scalar(select(EmailEventORM).where(or_(*filters)).limit(1))

            if existing is None:
                existing = EmailEventORM(
                    sender=email.sender,
                    subject=email.subject,
                    raw_content=email.body,
                    source_message_id=email.source_message_id,
                    source_imap_id=email.imap_id,
                    queue_status="new",
                )
                session.add(existing)
                session.flush()

            return self._queue_row(existing)

    def claim_new_for_queue(self, limit: int) -> list[dict[str, Any]]:
        """Claim FIFO email rows so only one enqueuer can send them."""
        with self._db.session() as session:
            rows = session.scalars(
                select(EmailEventORM)
                .where(EmailEventORM.queue_status == "new")
                .order_by(EmailEventORM.created_at.asc(), EmailEventORM.id.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            if not rows:
                return []

            ids = [row.id for row in rows]
            session.execute(
                update(EmailEventORM)
                .where(EmailEventORM.id.in_(ids))
                .values(queue_status="enqueueing", queue_error=None, updated_at=func.now())
            )

            return [self._queue_row(row) for row in rows]

    def mark_queued(self, email_id: Any, queue_message_id: str) -> None:
        with self._db.session() as session:
            session.execute(
                update(EmailEventORM)
                .where(EmailEventORM.id == email_id)
                .values(
                    queue_status="queued",
                    queue_message_id=queue_message_id,
                    queued_at=func.now(),
                    queue_error=None,
                    updated_at=func.now(),
                )
            )

    def mark_processing(self, email_id: Any, queue_message_id: str | None = None) -> None:
        values: dict[str, Any] = {
            "queue_status": "processing",
            "processing_started_at": func.now(),
            "queue_delivery_count": EmailEventORM.queue_delivery_count + 1,
            "queue_error": None,
            "updated_at": func.now(),
        }
        if queue_message_id is not None:
            values["queue_message_id"] = queue_message_id

        with self._db.session() as session:
            session.execute(update(EmailEventORM).where(EmailEventORM.id == email_id).values(**values))

    def mark_queue_processed(self, email_id: Any) -> None:
        with self._db.session() as session:
            session.execute(
                update(EmailEventORM)
                .where(EmailEventORM.id == email_id)
                .values(
                    queue_status="processed",
                    processed_at=func.now(),
                    queue_error=None,
                    updated_at=func.now(),
                )
            )

    def mark_queue_failed(self, email_id: Any, error: str, *, retryable: bool = True) -> None:
        with self._db.session() as session:
            session.execute(
                update(EmailEventORM)
                .where(EmailEventORM.id == email_id)
                .values(
                    queue_status="new" if retryable else "failed",
                    queue_error=error,
                    updated_at=func.now(),
                )
            )

    def get_queue_state(self, email_id: Any) -> dict[str, Any] | None:
        with self._db.session() as session:
            row = session.scalar(select(EmailEventORM).where(EmailEventORM.id == email_id))
        if row is None:
            return None
        return {
            "email_id": str(row.id),
            "queue_status": row.queue_status,
            "queue_message_id": row.queue_message_id,
            "queue_delivery_count": row.queue_delivery_count,
            "queue_error": row.queue_error,
            "processed_at": row.processed_at,
        }

    def update_extraction(self, email_id: Any, cmir: CMIR) -> None:
        with self._db.session() as session:
            session.execute(
                update(EmailEventORM)
                .where(EmailEventORM.id == email_id)
                .values(
                    extracted_json=cmir.model_dump(),
                    missing_fields=cmir.missing_fields,
                    status=cmir.status,
                    updated_at=func.now(),
                )
            )

    @staticmethod
    def _queue_row(row: EmailEventORM) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "sender": row.sender,
            "subject": row.subject,
            "raw_content": row.raw_content,
            "source_message_id": row.source_message_id,
            "source_imap_id": row.source_imap_id,
            "queue_status": row.queue_status,
            "created_at": row.created_at,
        }
