from __future__ import annotations

from dataclasses import dataclass

from langgraph.checkpoint.memory import MemorySaver

from cmir_agent.config import AppConfig
from cmir_agent.domain.validators import CMIRValidator
from cmir_agent.infrastructure.azure_openai import AzureOpenAICMIRExtractor
from cmir_agent.infrastructure.cli_human_review import CLIHumanReviewPort
from cmir_agent.infrastructure.database import Database
from cmir_agent.infrastructure.gmail_email_reader import GmailImapReader
from cmir_agent.infrastructure.postgres_observability import (
    PostgresAgentRunRepository,
    PostgresAgentTraceRepository,
    PostgresHITLActionRepository,
)
from cmir_agent.infrastructure.postgres_repositories import (
    PostgresActionLogRepository,
    PostgresCMIRRepository,
    PostgresEmailRepository,
)
from cmir_agent.interfaces.email_reader import EmailReader
from cmir_agent.interfaces.human_review import HumanReviewPort
from cmir_agent.interfaces.observability import (
    AgentRunRepository,
    AgentTraceRepository,
    HITLActionRepository,
)
from cmir_agent.workflow.graph import build_graph
from cmir_agent.workflow.nodes import WorkflowNodes


@dataclass
class Container:
    """Composition root: the only place that wires concrete infrastructure
    classes to interfaces. Everything downstream (nodes, graph, main) only
    ever sees the abstractions - this is what makes it possible to, say,
    swap AzureOpenAICMIRExtractor for a different provider, or
    CLIHumanReviewPort for a web-based one, by editing only this file.
    """

    config: AppConfig
    email_reader: EmailReader
    human_review: HumanReviewPort
    graph: object
    agent_runs: AgentRunRepository
    hitl_actions: HITLActionRepository

    @classmethod
    def build(cls) -> "Container":
        config = AppConfig.from_env()

        database = Database(config.database)
        email_reader = GmailImapReader(config.email)
        extractor = AzureOpenAICMIRExtractor(config.llm)
        validator = CMIRValidator()

        email_repository = PostgresEmailRepository(database)
        cmir_repository = PostgresCMIRRepository(database)
        action_log_repository = PostgresActionLogRepository(database)

        # Observability repositories - agent_runs / agent_trace / hitl_actions
        agent_run_repository = PostgresAgentRunRepository(database)
        agent_trace_repository: AgentTraceRepository = PostgresAgentTraceRepository(database)
        hitl_action_repository = PostgresHITLActionRepository(database)

        human_review = CLIHumanReviewPort()

        nodes = WorkflowNodes(
            email_reader=email_reader,
            extractor=extractor,
            validator=validator,
            email_repository=email_repository,
            cmir_repository=cmir_repository,
            action_log_repository=action_log_repository,
        )

        checkpointer = MemorySaver()
        graph = build_graph(nodes, checkpointer, agent_trace_repository)

        return cls(
            config=config,
            email_reader=email_reader,
            human_review=human_review,
            graph=graph,
            agent_runs=agent_run_repository,
            hitl_actions=hitl_action_repository,
        )
