from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from app.agents.cmir.nodes import WorkflowNodes
from app.agents.cmir.state import GraphState
from app.core.tracing import traced
from app.repositories.observability import PostgresAgentTraceRepository


def build_graph(
    nodes: WorkflowNodes,
    checkpointer: BaseCheckpointSaver,
    trace_repo: PostgresAgentTraceRepository,
):
    """Wires the node functions into the CMIR resolution graph (SCD2-aware).

    persist_email -> extract_cmir -> identify_existing_cmir -> prepare_diff
        -> validate_cmir -> persist_ai_result
        -> [needs_input] -> collect_missing_fields -> identify_existing_cmir (loop)
        -> [ready]        -> human_approval
                                -> [approved] -> persist_cmir
                                    -> [committed] -> mark_email_read -> END
                                    -> [conflict]  -> handle_version_conflict -> mark_email_read -> END
                                -> [rejected] -> persist_rejection -> mark_email_read -> END

    A checkpointer is required: it's what lets interrupt()/Command(resume=...)
    pause the graph mid-run and pick back up later with the human's answer.

    Every node is wrapped in traced(), so every execution - completed,
    paused on interrupt, or failed - is written to agent_trace. This is
    the only change from a plain node registration; the node functions
    themselves (in nodes.py) know nothing about tracing.

    Two sequencing decisions are load-bearing here, not stylistic (see
    docs/memory.md decisions #7 and #13 for the full reasoning):

    - identify_existing_cmir/prepare_diff run BEFORE validate_cmir, not after.
      validate_cmir decides which fields are "missing"; if the merge ran later, a
      field the LLM missed but the active record already has on file would be
      wrongly flagged as missing.
    - collect_missing_fields loops back to identify_existing_cmir, not straight to
      validate_cmir. A mandatory field like customer_identity can be blank on the
      very first pass (that's what triggers collect_missing_fields in the first
      place), so the first identify_existing_cmir call may have looked up the wrong
      entity. Re-entering through identify_existing_cmir keeps the lookup and diff
      accurate once the human fills in the missing identity field.
    """
    graph = StateGraph(GraphState)

    def node(name: str, fn):
        return traced(name, fn, trace_repo)

    graph.add_node("persist_email", node("persist_email", nodes.persist_email))
    graph.add_node("extract_cmir", node("extract_cmir", nodes.extract_cmir))
    graph.add_node("identify_existing_cmir", node("identify_existing_cmir", nodes.identify_existing_cmir))
    graph.add_node("prepare_diff", node("prepare_diff", nodes.prepare_diff))
    graph.add_node("validate_cmir", node("validate_cmir", nodes.validate_cmir))
    graph.add_node("persist_ai_result", node("persist_ai_result", nodes.persist_ai_result))
    graph.add_node("collect_missing_fields", node("collect_missing_fields", nodes.collect_missing_fields))
    graph.add_node("human_approval", node("human_approval", nodes.human_approval))
    graph.add_node("persist_cmir", node("persist_cmir", nodes.persist_cmir))
    graph.add_node("persist_rejection", node("persist_rejection", nodes.persist_rejection))
    graph.add_node("handle_version_conflict", node("handle_version_conflict", nodes.handle_version_conflict))
    graph.add_node("mark_email_read", node("mark_email_read", nodes.mark_email_read))

    graph.set_entry_point("persist_email")
    graph.add_edge("persist_email", "extract_cmir")
    graph.add_edge("extract_cmir", "identify_existing_cmir")
    graph.add_edge("identify_existing_cmir", "prepare_diff")
    graph.add_edge("prepare_diff", "validate_cmir")
    graph.add_edge("validate_cmir", "persist_ai_result")

    graph.add_conditional_edges(
        "persist_ai_result",
        nodes.route_after_validation,
        {
            "needs_input": "collect_missing_fields",
            "ready": "human_approval",
        },
    )

    # After the human supplies missing values, re-identify the active record before
    # re-validating -- the field they just supplied may be the identity field the
    # first lookup was missing (see the module docstring above).
    graph.add_edge("collect_missing_fields", "identify_existing_cmir")

    graph.add_conditional_edges(
        "human_approval",
        nodes.route_after_approval,
        {
            "approved": "persist_cmir",
            "rejected": "persist_rejection",
        },
    )

    graph.add_conditional_edges(
        "persist_cmir",
        nodes.route_after_persist_cmir,
        {
            "committed": "mark_email_read",
            "conflict": "handle_version_conflict",
        },
    )

    graph.add_edge("handle_version_conflict", "mark_email_read")
    graph.add_edge("persist_rejection", "mark_email_read")
    graph.add_edge("mark_email_read", END)

    return graph.compile(checkpointer=checkpointer)
