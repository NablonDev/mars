from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, ClassVar

# Previous implementation using MemorySaver.
# Replaced by PostgreSQL Checkpointer for durable LangGraph resume support.
# from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver

from app.agents.cmir.graph import build_graph
from app.agents.cmir.nodes import WorkflowNodes
from app.agents.po_validation.graph import build_po_validation_graph
from app.agents.po_validation.nodes import PoValidationNodes
from app.core.config import EmailConfig, LLMConfig, ServiceBusConfig, Settings, get_settings
from app.db.session import Database, checkpoint_dsn
from app.queue.producer import ServiceBusMailQueue
from app.repositories.action_log import PostgresActionLogRepository
from app.repositories.cmir import PostgresCMIRRepository
from app.repositories.email import PostgresEmailRepository
from app.repositories.observability import (
    PostgresAgentRunRepository,
    PostgresAgentTraceRepository,
    PostgresHITLActionRepository,
    PostgresHITLStateRepository,
    PostgresPendingHumanActionRepository,
    PostgresWorkflowThreadRepository,
)
from app.repositories.po_validation import (
    PostgresMaterialMasterRepository,
    PostgresPoLineErrorRepository,
    PostgresPoLineRepository,
)
from app.services.cli_human_review import CLIHumanReviewPort
from app.services.cmir_extractor import AzureOpenAICMIRExtractor
from app.services.cmir_validation import CMIRValidator
from app.services.email_reader import GmailImapReader

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """Composition root for the application and workflow runtime."""

    config: Settings
    email_reader: GmailImapReader
    human_review: CLIHumanReviewPort
    graph: Any
    agent_runs: PostgresAgentRunRepository
    hitl_actions: PostgresHITLActionRepository
    hitl_state: PostgresHITLStateRepository
    workflow_threads: PostgresWorkflowThreadRepository
    pending_human_actions: PostgresPendingHumanActionRepository
    email_repository: PostgresEmailRepository
    cmir_repository: PostgresCMIRRepository
    service_bus_queue: ServiceBusMailQueue
    po_validation_graph: Any
    po_lines: PostgresPoLineRepository
    material_master: PostgresMaterialMasterRepository
    po_line_errors: PostgresPoLineErrorRepository
    _resource_stack: ExitStack

    _instance: ClassVar[Container | None] = None

    @classmethod
    def build(cls) -> Container:
        if cls._instance is not None:
            return cls._instance

        config = get_settings()
        resources = ExitStack()

        database = Database(
            config.database_url,
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_timeout=config.db_pool_timeout,
        )
        email_reader = GmailImapReader(EmailConfig.from_settings(config))
        extractor = AzureOpenAICMIRExtractor(LLMConfig.from_settings(config))
        validator = CMIRValidator()

        email_repository = PostgresEmailRepository(database)
        cmir_repository = PostgresCMIRRepository(database)
        action_log_repository = PostgresActionLogRepository(database)

        agent_run_repository = PostgresAgentRunRepository(database)
        agent_trace_repository = PostgresAgentTraceRepository(database)
        hitl_action_repository = PostgresHITLActionRepository(database)
        hitl_state_repository = PostgresHITLStateRepository(database)
        workflow_thread_repository = PostgresWorkflowThreadRepository(database)
        pending_action_repository = PostgresPendingHumanActionRepository(database)

        human_review = CLIHumanReviewPort()
        service_bus_queue = ServiceBusMailQueue(ServiceBusConfig.from_settings(config))

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
            PostgresSaver.from_conn_string(checkpoint_dsn(config.database_url))
        )
        checkpointer.setup()
        logger.info("Checkpoint tables verified.")
        graph = build_graph(nodes, checkpointer, agent_trace_repository)
        logger.info("Graph compiled with PostgreSQL Checkpointer.")

        po_line_repository = PostgresPoLineRepository(database)
        material_master_repository = PostgresMaterialMasterRepository(database)
        po_line_error_repository = PostgresPoLineErrorRepository(database)

        po_validation_nodes = PoValidationNodes(
            po_line_repository=po_line_repository,
            material_master_repository=material_master_repository,
            cmir_repository=cmir_repository,
            po_line_error_repository=po_line_error_repository,
        )
        # Shares the same PostgresSaver checkpointer/connection as the CMIR graph;
        # checkpoint thread_ids are namespaced ("thread_po_...") so the two graphs
        # never collide in checkpoint storage.
        po_validation_graph = build_po_validation_graph(
            po_validation_nodes, checkpointer, agent_trace_repository
        )
        logger.info("PO Validation graph compiled with PostgreSQL Checkpointer.")

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
            cmir_repository=cmir_repository,
            service_bus_queue=service_bus_queue,
            po_validation_graph=po_validation_graph,
            po_lines=po_line_repository,
            material_master=material_master_repository,
            po_line_errors=po_line_error_repository,
            _resource_stack=resources,
        )
        return cls._instance

    @classmethod
    def close(cls) -> None:
        if cls._instance is None:
            return
        cls._instance._resource_stack.close()
        cls._instance = None
