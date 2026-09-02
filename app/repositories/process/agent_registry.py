"""Repository for the shared `process.agent`/`agent_run`/`agent_trace`
tables -- used by both the `penalties` and `cmir`/`po_validation` domains.
Was `app/repositories/agent_registry.py` (Agent/PromptVersion) plus the
`AgentRun`/`AgentTrace` methods split out of
`app/repositories/observability.py`'s `PostgresAgentRunRepository`/
`PostgresAgentTraceRepository`.

`app.models.process.agent.Agent` merges what were two tables (`agent` +
`prompt_version`) into one row per (agent_code, prompt_version) -- see that
model's docstring. `AgentRun` no longer carries the old `batch_id`/
`thread_id`/`email_id`/`po_line_id` columns (that grouping/subject
information now lives on `process.job_run`/`process.workflow_thread`, see
`app.repositories.process.workflow`); it gains a required `agent_id` FK
instead, since every run is now explicitly tied to one versioned agent row.

Switched from the old `Database`-per-call session pattern (each method
opening and committing its own session) to the project's standard
injected-`Session` pattern (see the `postgres-conventions` skill: "one
session per request/use-case, provided via Depends -- don't create ad hoc
sessions inside a repository") -- flagged in this PR's summary as a
deliberate, not purely mechanical, change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, AgentRun, AgentTrace
from app.utils.pagination import parse_cursor


def _agent_to_dict(row: Agent) -> dict:
    return {
        "id": row.id,
        "agent_code": row.agent_code,
        "agent_name": row.agent_name,
        "domain": row.domain,
        "prompt_version": row.prompt_version,
        "system_prompt": row.system_prompt,
        "description": row.description,
        "is_active": row.is_active,
    }


def _agent_run_to_dict(row: AgentRun) -> dict:
    return {
        "id": row.id,
        "job_item_id": row.job_item_id,
        "workflow_thread_id": row.workflow_thread_id,
        "agent_id": row.agent_id,
        "status": row.status,
        "run_type": row.run_type,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "error": row.error,
        "metadata_json": row.metadata_json,
        "updated_at": row.updated_at,
    }


def _agent_trace_to_dict(row: AgentTrace) -> dict:
    return {
        "id": row.id,
        "agent_run_id": row.agent_run_id,
        "node_name": row.node_name,
        "status": row.status,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "duration_ms": row.duration_ms,
        "input_snapshot": row.input_snapshot,
        "output_snapshot": row.output_snapshot,
        "error": row.error,
    }


class AgentRegistryRepository:
    """Repository for the merged agent/prompt-version registry."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_registered(
        self,
        agent_code: str,
        prompt_version: str,
        system_prompt: str,
        agent_name: str,
        domain: str,
        description: str | None = None,
        is_active: bool = True,
    ) -> UUID:
        """Idempotently register one (agent_code, prompt_version) row."""
        row = self._session.scalars(
            select(Agent).where(Agent.agent_code == agent_code, Agent.prompt_version == prompt_version)
        ).first()

        if row is None:
            row = Agent(
                agent_code=agent_code,
                agent_name=agent_name,
                domain=domain,
                prompt_version=prompt_version,
                system_prompt=system_prompt,
                description=description,
                is_active=is_active,
            )
            self._session.add(row)
            self._session.flush()

        return row.id

    def get_by_code_version(self, agent_code: str, prompt_version: str) -> dict | None:
        row = self._session.scalars(
            select(Agent).where(Agent.agent_code == agent_code, Agent.prompt_version == prompt_version)
        ).first()
        return _agent_to_dict(row) if row is not None else None

    def get_active(self, agent_code: str) -> dict | None:
        row = self._session.scalars(
            select(Agent).where(Agent.agent_code == agent_code, Agent.is_active.is_(True))
        ).first()
        return _agent_to_dict(row) if row is not None else None


class AgentRunRepository:
    """Repository for one independent agent execution."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start(
        self,
        agent_id: UUID,
        run_type: str,
        job_item_id: UUID | None = None,
        workflow_thread_id: UUID | None = None,
    ) -> UUID:
        row = AgentRun(
            agent_id=agent_id,
            run_type=run_type,
            job_item_id=job_item_id,
            workflow_thread_id=workflow_thread_id,
            status="running",
            metadata_json={},
        )
        self._session.add(row)
        self._session.flush()
        return row.id

    def update_status(
        self,
        run_id: UUID,
        status: str,
        *,
        error: str | None = None,
        completed: bool = False,
    ) -> None:
        row = self._session.get(AgentRun, run_id)
        if row is None:
            return

        row.status = status
        if error is not None:
            row.error = error
        if completed:
            from sqlalchemy import func

            row.completed_at = func.now()
        self._session.flush()

    def get(self, run_id: UUID) -> dict | None:
        row = self._session.get(AgentRun, run_id)
        return _agent_run_to_dict(row) if row is not None else None

    def list_runs(
        self,
        *,
        job_item_id: UUID | None = None,
        workflow_thread_id: UUID | None = None,
        agent_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        stmt = select(AgentRun)
        if job_item_id is not None:
            stmt = stmt.where(AgentRun.job_item_id == job_item_id)
        if workflow_thread_id is not None:
            stmt = stmt.where(AgentRun.workflow_thread_id == workflow_thread_id)
        if agent_id is not None:
            stmt = stmt.where(AgentRun.agent_id == agent_id)
        if status is not None:
            stmt = stmt.where(AgentRun.status == status)
        if cursor is not None:
            stmt = stmt.where(AgentRun.updated_at < parse_cursor(cursor))
        # Tiebreaker on id (see workflow.py's list_threads for why).
        stmt = stmt.order_by(AgentRun.updated_at.desc(), AgentRun.id.desc()).limit(limit)

        rows = self._session.scalars(stmt).all()
        items = [_agent_run_to_dict(r) for r in rows]
        next_cursor = items[-1]["updated_at"].isoformat() if len(items) == limit and items else None
        return items, next_cursor


class AgentTraceRepository:
    """Repository for one LangGraph node execution."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def log(
        self,
        agent_run_id: UUID,
        node_name: str,
        status: str,
        started_at: datetime,
        completed_at: datetime | None,
        duration_ms: int | None,
        input_snapshot: dict[str, Any] | None,
        output_snapshot: dict[str, Any] | None,
        error: str | None,
    ) -> dict:
        row = AgentTrace(
            agent_run_id=agent_run_id,
            node_name=node_name,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
            error=error,
        )
        self._session.add(row)
        self._session.flush()
        return _agent_trace_to_dict(row)

    def list_for_run(self, agent_run_id: UUID) -> list[dict]:
        rows = self._session.scalars(
            select(AgentTrace)
            .where(AgentTrace.agent_run_id == agent_run_id)
            .order_by(AgentTrace.started_at.asc())
        ).all()
        return [_agent_trace_to_dict(r) for r in rows]
