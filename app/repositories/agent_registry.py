"""Repository for the agent/prompt-version registry."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, PromptVersion


class PromptRegistryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_registered(
        self,
        agent_name: str,
        prompt_version: str,
        module_path: str,
        provider: str | None = None,
        source: str | None = None,
        description: str | None = None,
    ) -> UUID:
        """Idempotently register an agent and one of its prompt versions"""
        agent = self._session.scalars(
            select(Agent)
            .where(Agent.agent_name == agent_name)
        ).first()

        if agent is None:
            agent = Agent(
                agent_name=agent_name,
                source=source,
                description=description,
            )
            self._session.add(agent)
            self._session.flush()

        version = self._session.scalars(
            select(PromptVersion).where(
                PromptVersion.agent_id == agent.id,
                PromptVersion.prompt_version == prompt_version,
            )
        ).first()
        if version is None:
            self._session.add(
                PromptVersion(
                    agent_id=agent.id,
                    prompt_version=prompt_version,
                    module_path=module_path,
                    provider=provider,
                )
            )
            self._session.flush()

        return agent.id
