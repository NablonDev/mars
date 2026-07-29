from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from cmir_agent.interfaces.observability import AgentTraceRepository
from cmir_agent.workflow.nodes import WorkflowNodes
from cmir_agent.workflow.state import GraphState
from cmir_agent.workflow.tracing import traced


def build_graph(
    nodes: WorkflowNodes,
    checkpointer: BaseCheckpointSaver,
    trace_repo: AgentTraceRepository,
):
    """Wires the node functions into the CMIR resolution graph.

    persist_email -> extract_cmir -> validate_cmir -> persist_ai_result
        -> [needs_input] -> collect_missing_fields -> validate_cmir (loop)
        -> [ready]        -> human_approval
                                -> [approved] -> persist_cmir     -> mark_email_read -> END
                                -> [rejected] -> persist_rejection -> mark_email_read -> END

    A checkpointer is required: it's what lets interrupt()/Command(resume=...)
    pause the graph mid-run and pick back up later with the human's answer.

    Every node is wrapped in traced(), so every execution - completed,
    paused on interrupt, or failed - is written to agent_trace. This is
    the only change from a plain node registration; the node functions
    themselves (in nodes.py) know nothing about tracing.
    """
    graph = StateGraph(GraphState)

    def node(name: str, fn):
        return traced(name, fn, trace_repo)

    graph.add_node("persist_email", node("persist_email", nodes.persist_email))
    graph.add_node("extract_cmir", node("extract_cmir", nodes.extract_cmir))
    graph.add_node("validate_cmir", node("validate_cmir", nodes.validate_cmir))
    graph.add_node("persist_ai_result", node("persist_ai_result", nodes.persist_ai_result))
    graph.add_node("collect_missing_fields", node("collect_missing_fields", nodes.collect_missing_fields))
    graph.add_node("human_approval", node("human_approval", nodes.human_approval))
    graph.add_node("persist_cmir", node("persist_cmir", nodes.persist_cmir))
    graph.add_node("persist_rejection", node("persist_rejection", nodes.persist_rejection))
    graph.add_node("mark_email_read", node("mark_email_read", nodes.mark_email_read))

    graph.set_entry_point("persist_email")
    graph.add_edge("persist_email", "extract_cmir")
    graph.add_edge("extract_cmir", "validate_cmir")
    graph.add_edge("validate_cmir", "persist_ai_result")

    graph.add_conditional_edges(
        "persist_ai_result",
        nodes.route_after_validation,
        {
            "needs_input": "collect_missing_fields",
            "ready": "human_approval",
        },
    )

    # After the human supplies missing values, re-validate the completed
    # payload rather than re-running (and paying for) LLM extraction.
    graph.add_edge("collect_missing_fields", "validate_cmir")

    graph.add_conditional_edges(
        "human_approval",
        nodes.route_after_approval,
        {
            "approved": "persist_cmir",
            "rejected": "persist_rejection",
        },
    )

    graph.add_edge("persist_cmir", "mark_email_read")
    graph.add_edge("persist_rejection", "mark_email_read")
    graph.add_edge("mark_email_read", END)

    return graph.compile(checkpointer=checkpointer)