from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import or_, func, select, update

from cmir_agent.domain.models import CMIR, EmailMessage
from cmir_agent.infrastructure.database import Database
from cmir_agent.infrastructure.orm_models import (
    CMIRRecordORM,
    EmailActionLogORM,
    EmailEventORM,
)
from cmir_agent.interfaces.repositories import (
    ActionLogRepository,
    CMIRRepository,
    EmailRepository,
)


class PostgresEmailRepository(EmailRepository):
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

    def save_for_queue(self, email: EmailMessage) -> Dict[str, Any]:
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

    def claim_new_for_queue(self, limit: int) -> List[Dict[str, Any]]:
        """Claim FIFO email rows so only one enqueuer can send them."""
        with self._db.session() as session:
            rows = (
                session.scalars(
                    select(EmailEventORM)
                    .where(EmailEventORM.queue_status == "new")
                    .order_by(EmailEventORM.created_at.asc(), EmailEventORM.id.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
                .all()
            )
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

    def mark_processing(self, email_id: Any, queue_message_id: Optional[str] = None) -> None:
        values: Dict[str, Any] = {
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

    def get_queue_state(self, email_id: Any) -> Optional[Dict[str, Any]]:
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
    def _queue_row(row: EmailEventORM) -> Dict[str, Any]:
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


class PostgresCMIRRepository(CMIRRepository):
    """SQLAlchemy repository for approved CMIR records."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def save(self, email_id: Any, cmir: CMIR) -> int:
        with self._db.session() as session:
            existing = session.scalar(select(CMIRRecordORM).where(CMIRRecordORM.email_id == email_id))
            if existing is not None:
                return existing.id
            record = CMIRRecordORM(
                email_id=email_id,
                sender_type=cmir.sender_type,
                customer_identity=cmir.customer_identity,
                material_identity=cmir.material_identity,
                intent_phrase=cmir.intent_phrase,
                existing_cmir_ref=cmir.existing_cmir_ref,
                brand=cmir.brand,
                site=cmir.site,
                target_grd_code=cmir.target_grd_code,
                target_customer_material_ref=cmir.target_customer_material_ref,
                effective_date=cmir.effective_date or None,
                reason=cmir.reason,
            )
            session.add(record)
            session.flush()
            return record.id


class PostgresActionLogRepository(ActionLogRepository):
    """SQLAlchemy repository for email-level action audit events."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def log(self, email_id: Any, action: str, actor: str, details: Dict[str, Any]) -> None:
        with self._db.session() as session:
            session.add(
                EmailActionLogORM(
                    email_id=email_id,
                    action=action,
                    actor=actor,
                    details=details,
                )
            )
