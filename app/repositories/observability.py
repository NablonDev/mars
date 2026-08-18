from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.db.session import Database
from app.models.observability import (
    AgentRunORM,
    AgentTraceORM,
    HITLActionORM,
    PendingHumanActionORM,
    WorkflowThreadORM,
)
from app.schemas.cmir import PendingHumanAction, WorkflowThread

logger = logging.getLogger(__name__)


def _json_or_empty(value: Any) -> dict[str, Any]:
    """Normalize nullable JSON values returned by SQLAlchemy."""
    return value or {}


def _iso(value: Any) -> str | None:
    """Serialize database datetimes for API-facing dictionaries."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class PostgresAgentRunRepository:
    """SQLAlchemy repository for agent_runs."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def start(
        self,
        *,
        batch_id: str | None = None,
        thread_id: str | None = None,
        email_id: Any | None = None,
        po_line_id: Any | None = None,
        run_type: str = "email_ingest",
    ) -> UUID:
        """Create one per-email (or per-PO-line) agent run."""
        with self._db.session() as session:
            run = AgentRunORM(
                batch_id=batch_id,
                thread_id=thread_id,
                email_id=email_id,
                po_line_id=po_line_id,
                status="running",
                run_type=run_type,
                metadata_json={},
            )
            session.add(run)
            session.flush()
            run_id = run.id
        logger.info("Started email agent run %s for batch %s", run_id, batch_id)
        return run_id

    def update_status(
        self,
        run_id: UUID,
        status: str,
        *,
        email_id: Any | None = None,
        current_node: str | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": func.now()}
        if current_node is not None:
            values["current_node"] = current_node
        if email_id is not None:
            values["email_id"] = email_id
        if error is not None:
            values["error"] = error
        if completed:
            values["completed_at"] = func.now()

        with self._db.session() as session:
            session.execute(update(AgentRunORM).where(AgentRunORM.id == run_id).values(**values))
        logger.info("Updated email agent run %s to status %s", run_id, status)

    def update_thread_counts(
        self,
        run_id: UUID,
        *,
        total_threads: int | None = None,
        completed_threads: int | None = None,
        waiting_threads: int | None = None,
        failed_threads: int | None = None,
    ) -> None:
        values: dict[str, Any] = {"updated_at": func.now()}
        if total_threads is not None:
            values["total_threads"] = total_threads
        if completed_threads is not None:
            values["completed_threads"] = completed_threads
        if waiting_threads is not None:
            values["waiting_threads"] = waiting_threads
        if failed_threads is not None:
            values["failed_threads"] = failed_threads

        with self._db.session() as session:
            session.execute(update(AgentRunORM).where(AgentRunORM.id == run_id).values(**values))
        logger.info("Updated thread counts for agent batch run %s", run_id)

    def list_batches(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        stmt = (
            select(
                AgentRunORM.batch_id,
                func.count(AgentRunORM.id).label("total_threads"),
                func.count()
                .filter(AgentRunORM.status.in_(("waiting_missing_fields", "waiting_approval")))
                .label("waiting_threads"),
                func.count()
                .filter(AgentRunORM.status.in_(("completed_approved", "completed_rejected")))
                .label("completed_threads"),
                func.count().filter(AgentRunORM.status == "failed").label("failed_threads"),
                func.min(AgentRunORM.started_at).label("started_at"),
                func.max(AgentRunORM.updated_at).label("updated_at"),
            )
            .where(AgentRunORM.batch_id.is_not(None))
            .group_by(AgentRunORM.batch_id)
        )
        if status is not None:
            stmt = stmt.where(AgentRunORM.status == status)
        if cursor is not None:
            stmt = stmt.having(func.max(AgentRunORM.updated_at) < cursor)
        stmt = stmt.order_by(func.max(AgentRunORM.updated_at).desc()).limit(limit)

        with self._db.session() as session:
            rows = session.execute(stmt).all()

        items = [
            {
                "batch_id": row.batch_id,
                "status": "running",
                "total_threads": row.total_threads,
                "waiting_threads": row.waiting_threads,
                "completed_threads": row.completed_threads,
                "failed_threads": row.failed_threads,
                "started_at": _iso(row.started_at),
                "updated_at": _iso(row.updated_at),
            }
            for row in rows
        ]
        next_cursor = items[-1]["updated_at"] if len(items) == limit and items else None
        return items, next_cursor

    def list_agents(
        self,
        *,
        batch_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        stmt = select(AgentRunORM)
        if batch_id is not None:
            stmt = stmt.where(AgentRunORM.batch_id == batch_id)
        if status is not None:
            stmt = stmt.where(AgentRunORM.status == status)
        if cursor is not None:
            stmt = stmt.where(AgentRunORM.updated_at < cursor)
        stmt = stmt.order_by(AgentRunORM.updated_at.desc()).limit(limit)

        with self._db.session() as session:
            rows = session.scalars(stmt).all()

        items = [
            {
                "batch_id": row.batch_id,
                "agent_run_id": row.id,
                "thread_id": row.thread_id,
                "email_id": str(row.email_id) if row.email_id else None,
                "status": row.status,
                "current_node": row.current_node,
                "started_at": _iso(row.started_at),
                "updated_at": _iso(row.updated_at),
                "completed_at": _iso(row.completed_at),
                "error": row.error,
            }
            for row in rows
        ]
        next_cursor = items[-1]["updated_at"] if len(items) == limit and items else None
        return items, next_cursor


class PostgresWorkflowThreadRepository:
    """SQLAlchemy repository for workflow_threads."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def create(self, thread: WorkflowThread) -> str:
        with self._db.session() as session:
            record = WorkflowThreadORM(
                thread_id=thread.thread_id,
                batch_id=thread.batch_id,
                agent_run_id=thread.agent_run_id,
                email_id=thread.email_id,
                po_line_id=thread.po_line_id,
                source_message_id=thread.source_message_id,
                sender=thread.sender,
                subject=thread.subject,
                status=thread.status,
                current_node=thread.current_node,
                stage=thread.stage,
                cmir_status=thread.cmir_status,
                latest_snapshot=thread.latest_snapshot,
                pending_action_id=thread.pending_action_id,
                error=thread.error,
            )
            session.add(record)
            session.flush()
        logger.info("Created workflow thread %s for run %s", thread.thread_id, thread.agent_run_id)
        return thread.thread_id

    def update_status(
        self,
        thread_id: str,
        *,
        status: str,
        stage: str,
        current_node: str | None = None,
        cmir_status: str | None = None,
        latest_snapshot: dict[str, Any] | None = None,
        pending_action_id: int | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> None:
        values = self._thread_update_values(
            status=status,
            stage=stage,
            current_node=current_node,
            cmir_status=cmir_status,
            latest_snapshot=latest_snapshot,
            pending_action_id=pending_action_id,
            error=error,
            completed=completed,
        )
        with self._db.session() as session:
            session.execute(
                update(WorkflowThreadORM).where(WorkflowThreadORM.thread_id == thread_id).values(**values)
            )
        logger.info("Updated workflow thread %s to stage %s", thread_id, stage)

    def update_if_current(
        self,
        thread_id: str,
        *,
        expected_updated_at: str,
        status: str,
        stage: str,
        current_node: str | None = None,
        cmir_status: str | None = None,
        latest_snapshot: dict[str, Any] | None = None,
        pending_action_id: int | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> bool:
        values = self._thread_update_values(
            status=status,
            stage=stage,
            current_node=current_node,
            cmir_status=cmir_status,
            latest_snapshot=latest_snapshot,
            pending_action_id=pending_action_id,
            error=error,
            completed=completed,
        )
        with self._db.session() as session:
            result = session.execute(
                update(WorkflowThreadORM)
                .where(
                    and_(
                        WorkflowThreadORM.thread_id == thread_id,
                        WorkflowThreadORM.updated_at == expected_updated_at,
                    )
                )
                .values(**values)
            )
            updated = result.rowcount == 1
        logger.info("Optimistic update for workflow thread %s success=%s", thread_id, updated)
        return updated

    def get_by_thread_id(self, thread_id: str) -> WorkflowThread | None:
        with self._db.session() as session:
            row = session.scalar(select(WorkflowThreadORM).where(WorkflowThreadORM.thread_id == thread_id))
        if row is None:
            return None
        return WorkflowThread(
            thread_id=row.thread_id,
            agent_run_id=row.agent_run_id,
            batch_id=row.batch_id,
            email_id=str(row.email_id),
            source_message_id=row.source_message_id,
            sender=row.sender or "",
            subject=row.subject or "",
            status=row.status,
            current_node=row.current_node,
            stage=row.stage,
            cmir_status=row.cmir_status,
            latest_snapshot=_json_or_empty(row.latest_snapshot),
            pending_action_id=row.pending_action_id,
            error=row.error,
            po_line_id=row.po_line_id,
        )

    def get_by_email_id(self, email_id: Any) -> WorkflowThread | None:
        with self._db.session() as session:
            row = session.scalar(
                select(WorkflowThreadORM)
                .where(WorkflowThreadORM.email_id == email_id)
                .order_by(WorkflowThreadORM.updated_at.desc())
                .limit(1)
            )
        if row is None:
            return None
        return WorkflowThread(
            thread_id=row.thread_id,
            agent_run_id=row.agent_run_id,
            batch_id=row.batch_id,
            email_id=str(row.email_id),
            source_message_id=row.source_message_id,
            sender=row.sender or "",
            subject=row.subject or "",
            status=row.status,
            current_node=row.current_node,
            stage=row.stage,
            cmir_status=row.cmir_status,
            latest_snapshot=_json_or_empty(row.latest_snapshot),
            pending_action_id=row.pending_action_id,
            error=row.error,
            po_line_id=row.po_line_id,
        )

    def list_threads(
        self,
        *,
        batch_id: str | None = None,
        agent_run_id: UUID | None = None,
        status: str | None = None,
        stage: str | None = None,
        sender: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        stmt = select(WorkflowThreadORM)
        if batch_id is not None:
            stmt = stmt.where(WorkflowThreadORM.batch_id == batch_id)
        if agent_run_id is not None:
            stmt = stmt.where(WorkflowThreadORM.agent_run_id == agent_run_id)
        if status is not None:
            stmt = stmt.where(WorkflowThreadORM.status == status)
        if stage is not None:
            stmt = stmt.where(WorkflowThreadORM.stage == stage)
        if sender is not None:
            stmt = stmt.where(WorkflowThreadORM.sender == sender)
        if cursor is not None:
            stmt = stmt.where(WorkflowThreadORM.updated_at < cursor)
        stmt = stmt.order_by(WorkflowThreadORM.updated_at.desc()).limit(limit)

        with self._db.session() as session:
            rows = session.scalars(stmt).all()

        items = [
            {
                "batch_id": row.batch_id,
                "agent_run_id": row.agent_run_id,
                "thread_id": row.thread_id,
                "email_id": str(row.email_id) if row.email_id else None,
                "po_line_id": str(row.po_line_id) if row.po_line_id else None,
                "source_message_id": row.source_message_id,
                "sender": row.sender,
                "subject": row.subject,
                "stage": row.stage,
                "status": row.status,
                "current_node": row.current_node,
                "pending_action_id": row.pending_action_id,
                "updated_at": _iso(row.updated_at),
            }
            for row in rows
        ]
        next_cursor = items[-1]["updated_at"] if len(items) == limit and items else None
        return items, next_cursor

    def get_stage(self, thread_id: str) -> dict[str, Any] | None:
        with self._db.session() as session:
            row = session.scalar(select(WorkflowThreadORM).where(WorkflowThreadORM.thread_id == thread_id))
        if row is None:
            return None
        return {
            "batch_id": row.batch_id,
            "agent_run_id": row.agent_run_id,
            "thread_id": row.thread_id,
            "email_id": str(row.email_id) if row.email_id else None,
            "po_line_id": str(row.po_line_id) if row.po_line_id else None,
            "stage": row.stage,
            "status": row.status,
            "current_node": row.current_node,
            "pending_action_id": row.pending_action_id,
            "updated_at": _iso(row.updated_at),
            "source_message_id": row.source_message_id,
            "sender": row.sender,
            "subject": row.subject,
        }

    def get_snapshot(self, thread_id: str) -> dict[str, Any] | None:
        with self._db.session() as session:
            thread = session.scalar(select(WorkflowThreadORM).where(WorkflowThreadORM.thread_id == thread_id))
            if thread is None:
                return None
            history_rows = session.scalars(
                select(HITLActionORM)
                .where(HITLActionORM.thread_id == thread_id)
                .order_by(HITLActionORM.responded_at.asc())
            ).all()

        snapshot = _json_or_empty(thread.latest_snapshot)
        return {
            "batch_id": thread.batch_id,
            "agent_run_id": thread.agent_run_id,
            "thread_id": thread.thread_id,
            "email": {
                "email_id": str(thread.email_id),
                "sender": thread.sender,
                "subject": thread.subject,
                "source_message_id": thread.source_message_id,
            },
            "stage": thread.stage,
            "editable_fields": [
                "sender_type",
                "customer_identity",
                "material_identity",
                "intent_phrase",
                "existing_cmir_ref",
                "brand",
                "site",
                "target_grd_code",
                "target_customer_material_ref",
                "effective_date",
                "reason",
            ],
            "cmir": snapshot.get("cmir", {}),
            "existing_cmir": snapshot.get("existing_cmir"),
            "diff": snapshot.get("diff", {}),
            "history": [
                {
                    "actor": row.actor,
                    "action_type": row.action_type,
                    "field_changes": _json_or_empty(row.field_changes),
                    "created_at": _iso(row.responded_at),
                }
                for row in history_rows
            ],
            "updated_at": _iso(thread.updated_at),
        }

    @staticmethod
    def _thread_update_values(
        *,
        status: str,
        stage: str,
        current_node: str | None,
        cmir_status: str | None,
        latest_snapshot: dict[str, Any] | None,
        pending_action_id: int | None,
        error: str | None,
        completed: bool,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {"status": status, "stage": stage, "updated_at": func.now()}
        if completed:
            values["current_node"] = None
            values["pending_action_id"] = None
            values["completed_at"] = func.now()
        elif current_node is not None:
            values["current_node"] = current_node
        if cmir_status is not None:
            values["cmir_status"] = cmir_status
        if latest_snapshot is not None:
            values["latest_snapshot"] = latest_snapshot
        if pending_action_id is not None:
            values["pending_action_id"] = pending_action_id
        if error is not None:
            values["error"] = error
        return values


class PostgresPendingHumanActionRepository:
    """SQLAlchemy repository for pending_human_actions."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def create_open(self, action: PendingHumanAction) -> int:
        with self._db.session() as session:
            record = PendingHumanActionORM(
                batch_id=action.batch_id,
                agent_run_id=action.agent_run_id,
                thread_id=action.thread_id,
                email_id=action.email_id,
                po_line_id=action.po_line_id,
                interrupt_type=action.interrupt_type,
                payload=action.payload,
                state_snapshot=action.state_snapshot,
                status="open",
            )
            session.add(record)
            session.flush()
            action_id = record.id
        logger.info("Created pending human action %s for thread %s", action_id, action.thread_id)
        return action_id

    def complete(
        self,
        action_id: int,
        *,
        answer: dict[str, Any],
        actor: str,
    ) -> None:
        with self._db.session() as session:
            session.execute(
                update(PendingHumanActionORM)
                .where(
                    and_(
                        PendingHumanActionORM.id == action_id,
                        PendingHumanActionORM.status == "open",
                    )
                )
                .values(status="completed", answer=answer, actor=actor, completed_at=func.now())
            )
        logger.info("Completed pending human action %s", action_id)

    def get_open_for_thread(self, thread_id: str) -> dict[str, Any] | None:
        with self._db.session() as session:
            row = session.scalar(
                select(PendingHumanActionORM).where(
                    and_(
                        PendingHumanActionORM.thread_id == thread_id,
                        PendingHumanActionORM.status == "open",
                    )
                )
            )
        if row is None:
            return None
        return {
            "id": row.id,
            "batch_id": row.batch_id,
            "agent_run_id": row.agent_run_id,
            "thread_id": row.thread_id,
            "email_id": str(row.email_id) if row.email_id else None,
            "po_line_id": str(row.po_line_id) if row.po_line_id else None,
            "interrupt_type": row.interrupt_type,
            "payload": _json_or_empty(row.payload),
            "state_snapshot": _json_or_empty(row.state_snapshot),
            "created_at": _iso(row.created_at),
        }


class PostgresAgentTraceRepository:
    """SQLAlchemy repository for agent_traces."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def log(
        self,
        run_id: UUID,
        node_name: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: int,
        input_snapshot: dict[str, Any] | None,
        output_snapshot: dict[str, Any] | None,
        error: str | None,
        batch_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        with self._db.session() as session:
            session.add(
                AgentTraceORM(
                    run_id=run_id,
                    batch_id=batch_id,
                    thread_id=thread_id,
                    node_name=node_name,
                    status=status,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    input_snapshot=input_snapshot,
                    output_snapshot=output_snapshot,
                    error=error,
                )
            )


class PostgresHITLActionRepository:
    """SQLAlchemy repository for hitl_actions."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def log(
        self,
        run_id: UUID,
        email_id: Any | None,
        interrupt_type: str,
        question: dict[str, Any],
        answer: dict[str, Any],
        actor: str,
        decision: str | None = None,
        reason: str | None = None,
        batch_id: str | None = None,
        thread_id: str | None = None,
        action_type: str | None = None,
        field_changes: dict[str, Any] | None = None,
        po_line_id: str | None = None,
    ) -> None:
        with self._db.session() as session:
            session.add(
                HITLActionORM(
                    run_id=run_id,
                    batch_id=batch_id,
                    email_id=email_id,
                    po_line_id=po_line_id,
                    interrupt_type=interrupt_type,
                    question=question,
                    answer=answer,
                    decision=decision,
                    reason=reason,
                    actor=actor,
                    thread_id=thread_id,
                    action_type=action_type,
                    field_changes=field_changes,
                )
            )

    def list_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        with self._db.session() as session:
            rows = session.scalars(
                select(HITLActionORM)
                .where(HITLActionORM.thread_id == thread_id)
                .order_by(HITLActionORM.responded_at.asc())
            ).all()
        return [
            {
                "actor": row.actor,
                "action_type": row.action_type,
                "field_changes": _json_or_empty(row.field_changes),
                "created_at": _iso(row.responded_at),
            }
            for row in rows
        ]


class PostgresHITLStateRepository:
    """Transactional repository for reviewer actions and thread transitions."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def apply_human_action(
        self,
        *,
        run_id: UUID,
        batch_id: str | None,
        thread_id: str,
        email_id: str | None,
        pending_action_id: int,
        interrupt_type: str,
        question: dict[str, Any],
        answer: dict[str, Any],
        actor: str,
        action_type: str,
        next_status: str,
        next_stage: str,
        next_current_node: str | None = None,
        next_cmir_status: str | None = None,
        next_latest_snapshot: dict[str, Any] | None = None,
        next_pending_interrupt_type: str | None = None,
        next_pending_payload: dict[str, Any] | None = None,
        next_pending_state_snapshot: dict[str, Any] | None = None,
        decision: str | None = None,
        reason: str | None = None,
        field_changes: dict[str, Any] | None = None,
        completed: bool = False,
        po_line_id: str | None = None,
    ) -> int | None:
        with self._db.session() as session:
            result = session.execute(
                update(PendingHumanActionORM)
                .where(
                    and_(
                        PendingHumanActionORM.id == pending_action_id,
                        PendingHumanActionORM.thread_id == thread_id,
                        PendingHumanActionORM.status == "open",
                    )
                )
                .values(status="completed", answer=answer, actor=actor, completed_at=func.now())
            )
            if result.rowcount != 1:
                raise ValueError(f"Open pending human action {pending_action_id} not found for {thread_id}")

            session.add(
                HITLActionORM(
                    run_id=run_id,
                    batch_id=batch_id,
                    email_id=email_id,
                    po_line_id=po_line_id,
                    interrupt_type=interrupt_type,
                    question=question,
                    answer=answer,
                    decision=decision,
                    reason=reason,
                    actor=actor,
                    thread_id=thread_id,
                    action_type=action_type,
                    field_changes=field_changes,
                )
            )

            new_pending_action_id: int | None = None
            if next_pending_interrupt_type is not None:
                if next_pending_payload is None or next_pending_state_snapshot is None:
                    raise ValueError("Next pending action requires payload and state snapshot.")
                pending_record = PendingHumanActionORM(
                    batch_id=batch_id or "",
                    agent_run_id=run_id,
                    thread_id=thread_id,
                    email_id=email_id,
                    po_line_id=po_line_id,
                    interrupt_type=next_pending_interrupt_type,
                    payload=next_pending_payload,
                    state_snapshot=next_pending_state_snapshot,
                    status="open",
                )
                session.add(pending_record)
                session.flush()
                new_pending_action_id = pending_record.id

            thread_values = PostgresWorkflowThreadRepository._thread_update_values(
                status=next_status,
                stage=next_stage,
                current_node=next_current_node,
                cmir_status=next_cmir_status,
                latest_snapshot=next_latest_snapshot,
                pending_action_id=new_pending_action_id,
                error=None,
                completed=completed,
            )
            session.execute(
                update(WorkflowThreadORM)
                .where(WorkflowThreadORM.thread_id == thread_id)
                .values(**thread_values)
            )

            agent_values: dict[str, Any] = {
                "status": next_status,
                "updated_at": func.now(),
                "error": None,
            }
            if next_current_node is not None and not completed:
                agent_values["current_node"] = next_current_node
            elif completed:
                agent_values["current_node"] = None
                agent_values["completed_at"] = func.now()

            session.execute(update(AgentRunORM).where(AgentRunORM.id == run_id).values(**agent_values))

        logger.info(
            "Applied human action for thread %s and transitioned to %s",
            thread_id,
            next_stage,
        )
        return new_pending_action_id
