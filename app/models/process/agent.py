"""Registry of LLM agents, merging what were two tables (`agent` +
`prompt_version`) into one: one row per (agent, prompt version) pair.

`process.agent.system_prompt` is a new persistent store of LLM
system-level instructions -- a future prompt-injection surface once the
runtime load lands (see the approved Phase 1 plan's security note). No
live risk in Phase 1 (seeded only from version-controlled source in
`scripts/seed/seed_agents.py`), but no user-writable path may ever reach
this column and no API endpoint may expose a write to it.

`uq_agent_one_active_per_code` -- a partial unique index on `(agent_code)
WHERE is_active`, guaranteeing at most one active prompt version per agent
-- is migration-only raw DDL (see the `process` schema revision), never a
model declaration: it's the same "postgresql_where= is silently dropped on
SQLite" problem as `cmir_record`'s and `job_item`'s partial indexes.
"""

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import PROCESS_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7
from app.models.enums import AgentDomain


def _check_domain_in_sql() -> str:
    """Build a deterministic SQL IN expression from `AgentDomain`, same
    helper style as `app/models/process/job.py::_check_in_sql` (sorted
    values keep generated DDL deterministic across runs). Unlike
    `workflow_thread_subject`'s `num_nonnulls` CHECK, a plain `IN (...)`
    expression is portable SQL -- it compiles safely on SQLite too, so
    this stays a model-level `CheckConstraint`, not migration-only DDL."""
    values = ", ".join(f"'{value.value}'" for value in sorted(AgentDomain))
    return f"domain IN ({values})"


class Agent(Base, TimestampMixin):
    """One versioned LLM-backed agent/feature in this codebase."""

    __tablename__ = "agent"
    __table_args__ = (
        UniqueConstraint("agent_code", "prompt_version", name="uq_agent_code_prompt_version"),
        CheckConstraint(_check_domain_in_sql(), name="ck_agent_domain"),
        {"schema": PROCESS_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    agent_code: Mapped[str] = mapped_column(String(100), index=True)
    agent_name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(50))
    prompt_version: Mapped[str] = mapped_column(String(50))
    system_prompt: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
