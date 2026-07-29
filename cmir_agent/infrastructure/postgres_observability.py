from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from cmir_agent.infrastructure.database import Database
from cmir_agent.interfaces.observability import (
    AgentRunRepository,
    AgentTraceRepository,
    HITLActionRepository,
)


class PostgresAgentRunRepository(AgentRunRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    def start(self, thread_id: str) -> int:
        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_runs (thread_id, status)
                VALUES (%s, 'running')
                RETURNING id
                """,
                (thread_id,),
            )
            return cur.fetchone()[0]

    def update_status(
        self,
        run_id: int,
        status: str,
        *,
        email_id: Optional[int] = None,
        current_node: Optional[str] = None,
        error: Optional[str] = None,
        completed: bool = False,
    ) -> None:
        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_runs
                SET status = %s,
                    email_id = COALESCE(%s, email_id),
                    current_node = COALESCE(%s, current_node),
                    error = COALESCE(%s, error),
                    updated_at = NOW(),
                    completed_at = CASE WHEN %s THEN NOW() ELSE completed_at END
                WHERE id = %s
                """,
                (status, email_id, current_node, error, completed, run_id),
            )


class PostgresAgentTraceRepository(AgentTraceRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    def log(
        self,
        run_id: int,
        node_name: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: int,
        input_snapshot: Optional[Dict[str, Any]],
        output_snapshot: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_trace (
                    run_id, node_name, status, started_at, completed_at,
                    duration_ms, input_snapshot, output_snapshot, error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    node_name,
                    status,
                    started_at,
                    completed_at,
                    duration_ms,
                    json.dumps(input_snapshot) if input_snapshot is not None else None,
                    json.dumps(output_snapshot) if output_snapshot is not None else None,
                    error,
                ),
            )


class PostgresHITLActionRepository(HITLActionRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    def log(
        self,
        run_id: int,
        email_id: Optional[int],
        interrupt_type: str,
        question: Dict[str, Any],
        answer: Dict[str, Any],
        actor: str,
        decision: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hitl_actions (
                    run_id, email_id, interrupt_type, question, answer,
                    decision, reason, actor
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    email_id,
                    interrupt_type,
                    json.dumps(question),
                    json.dumps(answer),
                    decision,
                    reason,
                    actor,
                ),
            )
