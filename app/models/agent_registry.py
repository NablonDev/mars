"""Registry of LLM agents and the prompt versions they use."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7


class Agent(Base):
    """One LLM-backed feature/agent in this codebase."""

    __tablename__ = "agent"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    agent_name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    # "fine_projection" | "fine_mitigation" | "cmir" | "po_validation"
    source: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PromptVersion(Base):
    """One versioned prompt belonging to an agent"""

    __tablename__ = "prompt_version"
    __table_args__ = (
        UniqueConstraint("agent_id", "prompt_version", name="uq_prompt_version_agent_version"),
        {"schema": FINES_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    agent_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{FINES_SCHEMA}.agent.id"))
    prompt_version: Mapped[str] = mapped_column(String(20))
    module_path: Mapped[str] = mapped_column(String(200))  # e.g. "app.agents.fine_projection.prompts.v3"
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "azure_openai"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
