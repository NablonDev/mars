"""Registry of LLM agents with versioned prompts."""

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import PROCESS_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7
from app.models.enums import AgentDomain


def _check_domain_in_sql() -> str:
    """Build a portable, deterministic SQL IN expression from `AgentDomain`'s own members."""
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
