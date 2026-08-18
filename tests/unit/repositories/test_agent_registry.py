"""Tests for PromptRegistryRepository -- the source of dim_agent/dim_prompt_version rows."""

from sqlalchemy import func, select

from app.models import Agent, PromptVersion
from app.repositories.agent_registry import PromptRegistryRepository


def test_ensure_registered_creates_agent_and_prompt_version(db_session):
    repo = PromptRegistryRepository(db_session)

    agent_id = repo.ensure_registered(
        agent_name="fine_summary",
        prompt_version="v2",
        module_path="app.agents.prompts.fine_summary.v2",
        provider="azure_openai",
        source="fines",
    )

    agent = db_session.scalars(select(Agent).where(Agent.agent_name == "fine_summary")).first()
    assert agent is not None
    assert agent.id == agent_id
    assert agent.source == "fines"

    version = db_session.scalars(
        select(PromptVersion).where(
            PromptVersion.agent_id == agent_id,
            PromptVersion.prompt_version == "v2",
        )
    ).first()
    assert version is not None
    assert version.module_path == "app.agents.prompts.fine_summary.v2"
    assert version.provider == "azure_openai"


def test_ensure_registered_defaults_source_to_fines(db_session):
    repo = PromptRegistryRepository(db_session)

    repo.ensure_registered(
        agent_name="fine_summary",
        prompt_version="v2",
        module_path="app.agents.prompts.fine_summary.v2",
    )

    agent = db_session.scalars(select(Agent).where(Agent.agent_name == "fine_summary")).first()
    assert agent.source == "fines"


def test_ensure_registered_is_idempotent(db_session):
    """Called on the service's own read/write path -- must never duplicate
    rows across repeated calls."""
    repo = PromptRegistryRepository(db_session)

    agent_ids = [
        repo.ensure_registered(
            agent_name="fine_summary",
            prompt_version="v2",
            module_path="app.agents.prompts.fine_summary.v2",
        )
        for _ in range(3)
    ]

    assert len(set(agent_ids)) == 1
    agent_count = db_session.scalar(select(func.count()).select_from(Agent))
    version_count = db_session.scalar(select(func.count()).select_from(PromptVersion))
    assert agent_count == 1
    assert version_count == 1


def test_ensure_registered_adds_a_new_version_under_an_existing_agent(db_session):
    repo = PromptRegistryRepository(db_session)
    first_agent_id = repo.ensure_registered(
        agent_name="fine_summary",
        prompt_version="v1",
        module_path="app.agents.prompts.fine_summary.v1",
    )
    second_agent_id = repo.ensure_registered(
        agent_name="fine_summary",
        prompt_version="v2",
        module_path="app.agents.prompts.fine_summary.v2",
    )

    assert first_agent_id == second_agent_id
    agent_count = db_session.scalar(select(func.count()).select_from(Agent))
    version_count = db_session.scalar(select(func.count()).select_from(PromptVersion))
    assert agent_count == 1
    assert version_count == 2
