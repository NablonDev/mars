"""Tests for AgentRegistryRepository -- the source of `process.agent` rows
(one row per (agent_code, prompt_version) pair). Was
tests/unit/repositories/test_agent_registry.py against
`PromptRegistryRepository` (`Agent`/`PromptVersion`, two tables) --
relocated onto the merged `process.agent` table (see
app/models/process/agent.py's docstring)."""

import pytest
from sqlalchemy import func, select

from app.models import Agent
from app.repositories.process.agent_registry import AgentRegistryRepository


def test_ensure_registered_creates_an_agent_row(db_session):
    repo = AgentRegistryRepository(db_session)

    agent_id = repo.ensure_registered(
        agent_code="fine_projection_summary",
        prompt_version="v2",
        system_prompt="You are a penalty-projection summarizer.",
        agent_name="Penalty Projection Summary",
        domain="penalties",
        description="Generates the projection summary narrative.",
    )

    agent = db_session.scalars(select(Agent).where(Agent.agent_code == "fine_projection_summary")).first()
    assert agent is not None
    assert agent.id == agent_id
    assert agent.domain == "penalties"
    assert agent.description == "Generates the projection summary narrative."


def test_ensure_registered_defaults_description_to_none(db_session):
    repo = AgentRegistryRepository(db_session)

    repo.ensure_registered(
        agent_code="fine_projection_summary",
        prompt_version="v2",
        system_prompt="You are a penalty-projection summarizer.",
        agent_name="Penalty Projection Summary",
        domain="penalties",
    )

    agent = db_session.scalars(select(Agent).where(Agent.agent_code == "fine_projection_summary")).first()
    assert agent.description is None


def test_ensure_registered_requires_domain(db_session):
    """`domain` has no default -- `process.agent.domain` is a NOT NULL column
    restricted by `ck_agent_domain` to ('cmir', 'penalties'); there is no
    sensible default between the two, so every caller must say which."""
    repo = AgentRegistryRepository(db_session)

    with pytest.raises(TypeError):
        repo.ensure_registered(
            agent_code="fine_projection_summary",
            prompt_version="v2",
            system_prompt="You are a penalty-projection summarizer.",
            agent_name="Penalty Projection Summary",
        )


def test_ensure_registered_is_idempotent(db_session):
    """Called on the service's own read/write path -- must never duplicate
    rows across repeated calls."""
    repo = AgentRegistryRepository(db_session)

    agent_ids = [
        repo.ensure_registered(
            agent_code="fine_projection_summary",
            prompt_version="v2",
            system_prompt="You are a penalty-projection summarizer.",
            agent_name="Penalty Projection Summary",
            domain="penalties",
        )
        for _ in range(3)
    ]

    assert len(set(agent_ids)) == 1
    agent_count = db_session.scalar(select(func.count()).select_from(Agent))
    assert agent_count == 1


def test_ensure_registered_adds_a_new_row_under_the_same_agent_code(db_session):
    repo = AgentRegistryRepository(db_session)
    first_agent_id = repo.ensure_registered(
        agent_code="fine_projection_summary",
        prompt_version="v1",
        system_prompt="v1 prompt",
        agent_name="Penalty Projection Summary",
        domain="penalties",
    )
    second_agent_id = repo.ensure_registered(
        agent_code="fine_projection_summary",
        prompt_version="v2",
        system_prompt="v2 prompt",
        agent_name="Penalty Projection Summary",
        domain="penalties",
    )

    assert first_agent_id != second_agent_id
    agent_count = db_session.scalar(select(func.count()).select_from(Agent))
    assert agent_count == 2


def test_get_active_returns_only_the_active_row(db_session):
    repo = AgentRegistryRepository(db_session)
    repo.ensure_registered(
        agent_code="fine_projection_summary",
        prompt_version="v1",
        system_prompt="v1 prompt",
        agent_name="Penalty Projection Summary",
        domain="penalties",
        is_active=False,
    )
    repo.ensure_registered(
        agent_code="fine_projection_summary",
        prompt_version="v2",
        system_prompt="v2 prompt",
        agent_name="Penalty Projection Summary",
        domain="penalties",
        is_active=True,
    )

    active = repo.get_active("fine_projection_summary")
    assert active["prompt_version"] == "v2"


def test_get_by_code_version_returns_none_when_missing(db_session):
    repo = AgentRegistryRepository(db_session)
    assert repo.get_by_code_version("does_not_exist", "v1") is None
