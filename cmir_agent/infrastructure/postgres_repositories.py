from __future__ import annotations

import json
from typing import Any, Dict

from cmir_agent.domain.models import CMIR, EmailMessage
from cmir_agent.infrastructure.database import Database
from cmir_agent.interfaces.repositories import (
    ActionLogRepository,
    CMIRRepository,
    EmailRepository,
)


class PostgresEmailRepository(EmailRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    def save(self, email: EmailMessage) -> int:
        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO email_events (sender, subject, raw_content)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (email.sender, email.subject, email.body),
            )
            return cur.fetchone()[0]

    def update_extraction(self, email_id: int, cmir: CMIR) -> None:
        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE email_events
                SET extracted_json = %s,
                    missing_fields = %s,
                    status = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    json.dumps(cmir.model_dump()),
                    json.dumps(cmir.missing_fields),
                    cmir.status,
                    email_id,
                ),
            )


class PostgresCMIRRepository(CMIRRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    def save(self, email_id: int, cmir: CMIR) -> int:
        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cmir_records (
                    email_id, sender_type, customer_identity, material_identity,
                    intent_phrase, existing_cmir_ref, brand, site,
                    target_grd_code, target_customer_material_ref,
                    effective_date, reason
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    email_id,
                    cmir.sender_type,
                    cmir.customer_identity,
                    cmir.material_identity,
                    cmir.intent_phrase,
                    cmir.existing_cmir_ref,
                    cmir.brand,
                    cmir.site,
                    cmir.target_grd_code,
                    cmir.target_customer_material_ref,
                    cmir.effective_date or None,
                    cmir.reason,
                ),
            )
            return cur.fetchone()[0]


class PostgresActionLogRepository(ActionLogRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    def log(self, email_id: int, action: str, actor: str, details: Dict[str, Any]) -> None:
        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO email_action_log (email_id, action, actor, details)
                VALUES (%s, %s, %s, %s)
                """,
                (email_id, action, actor, json.dumps(details)),
            )
