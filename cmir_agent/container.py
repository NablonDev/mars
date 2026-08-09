from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from typing import ClassVar, Optional

# Previous implementation using MemorySaver.
# Replaced by PostgreSQL Checkpointer for durable LangGraph resume support.
# from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver

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
    PostgresHITLStateRepository,
    PostgresPendingHumanActionRepository,
    PostgresWorkflowThreadRepository,
)
from cmir_agent.infrastructure.postgres_repositories import (
    PostgresActionLogRepository,
    PostgresCMIRRepository,
    PostgresEmailRepository,
)
from cmir_agent.infrastructure.service_bus import ServiceBusMailQueue
from cmir_agent.interfaces.email_reader import EmailReader
from cmir_agent.interfaces.human_review import HumanReviewPort
from cmir_agent.interfaces.observability import (
    AgentRunRepository,
    AgentTraceRepository,
    HITLActionRepository,
    HITLStateRepository,
    PendingHumanActionRepository,
    WorkflowThreadRepository,
)
from cmir_agent.interfaces.repositories import EmailRepository
from cmir_agent.workflow.graph import build_graph
from cmir_agent.workflow.nodes import WorkflowNodes

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """Composition root for the application and workflow runtime."""

    config: AppConfig
    email_reader: EmailReader
    human_review: HumanReviewPort
    graph: object
    agent_runs: AgentRunRepository
    hitl_actions: HITLActionRepository
    hitl_state: HITLStateRepository
    workflow_threads: WorkflowThreadRepository
    pending_human_actions: PendingHumanActionRepository
    email_repository: EmailRepository
    service_bus_queue: ServiceBusMailQueue
    _resource_stack: ExitStack

    _instance: ClassVar[Optional["Container"]] = None

    @classmethod
    def build(cls) -> "Container":
        if cls._instance is not None:
            return cls._instance

        config = AppConfig.from_env()
        resources = ExitStack()

        database = Database(config.database)
        email_reader = GmailImapReader(config.email)
        extractor = AzureOpenAICMIRExtractor(config.llm)
        validator = CMIRValidator()

        email_repository = PostgresEmailRepository(database)
        cmir_repository = PostgresCMIRRepository(database)
        action_log_repository = PostgresActionLogRepository(database)

        agent_run_repository = PostgresAgentRunRepository(database)
        agent_trace_repository: AgentTraceRepository = PostgresAgentTraceRepository(database)
        hitl_action_repository = PostgresHITLActionRepository(database)
        hitl_state_repository = PostgresHITLStateRepository(database)
        workflow_thread_repository = PostgresWorkflowThreadRepository(database)
        pending_action_repository = PostgresPendingHumanActionRepository(database)

        human_review = CLIHumanReviewPort()
        service_bus_queue = ServiceBusMailQueue(config.service_bus)

        nodes = WorkflowNodes(
            email_reader=email_reader,
            extractor=extractor,
            validator=validator,
            email_repository=email_repository,
            cmir_repository=cmir_repository,
            action_log_repository=action_log_repository,
            workflow_thread_repository=workflow_thread_repository,
        )

        logger.info("Initializing LangGraph PostgreSQL Checkpointer...")
        # Previous implementation using MemorySaver kept for easy rollback.
        # checkpointer = MemorySaver()
        checkpointer = resources.enter_context(
            PostgresSaver.from_conn_string(config.database.checkpoint_url())
        )
        checkpointer.setup()
        logger.info("Checkpoint tables verified.")
        graph = build_graph(nodes, checkpointer, agent_trace_repository)
        logger.info("Graph compiled with PostgreSQL Checkpointer.")

        cls._instance = cls(
            config=config,
            email_reader=email_reader,
            human_review=human_review,
            graph=graph,
            agent_runs=agent_run_repository,
            hitl_actions=hitl_action_repository,
            hitl_state=hitl_state_repository,
            workflow_threads=workflow_thread_repository,
            pending_human_actions=pending_action_repository,
            email_repository=email_repository,
            service_bus_queue=service_bus_queue,
            _resource_stack=resources,
        )
        return cls._instance

    @classmethod
    def close(cls) -> None:
        if cls._instance is None:
            return
        cls._instance._resource_stack.close()
        cls._instance = None
