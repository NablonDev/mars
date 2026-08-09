from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from langgraph.errors import GraphInterrupt

from cmir_agent.interfaces.observability import AgentTraceRepository
from cmir_agent.workflow.state import GraphState

NodeFn = Callable[[GraphState], GraphState]


def traced(node_name: str, fn: NodeFn, trace_repo: AgentTraceRepository) -> NodeFn:
    """Wrap a node function so every execution is written to agent_trace.

    Three outcomes are logged, distinguished by `status`:
    - "completed" - the node returned normally
    - "paused"    - the node hit interrupt() and the graph is waiting on a human
    - "failed"    - the node raised a real exception

    Note: because of how LangGraph's interrupt() replay works, a node that
    pauses gets called twice across the run - once up to the pause
    ("paused"), and once again on resume, from the top, this time running
    to completion ("completed"). That's expected, not a bug: the gap
    between those two trace rows is effectively "how long did the human
    take to answer."

    GraphInterrupt is LangGraph's internal signal for a pause. If a
    langgraph upgrade moves/renames it, this import is the one line to
    fix - nothing else in the project touches LangGraph internals.
    """

    def wrapped(state: GraphState) -> GraphState:
        run_id = state.get("run_id")
        batch_id = state.get("batch_id")
        thread_id = state.get("thread_id")
        started_at = datetime.now(timezone.utc)
        t0 = time.perf_counter()

        try:
            result = fn(state)
        except GraphInterrupt:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            if run_id is not None:
                trace_repo.log(
                    run_id, node_name, "paused",
                    started_at, datetime.now(timezone.utc), duration_ms,
                    input_snapshot=state, output_snapshot=None, error=None,
                    batch_id=batch_id, thread_id=thread_id,
                )
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            if run_id is not None:
                trace_repo.log(
                    run_id, node_name, "failed",
                    started_at, datetime.now(timezone.utc), duration_ms,
                    input_snapshot=state, output_snapshot=None, error=str(exc),
                    batch_id=batch_id, thread_id=thread_id,
                )
            raise
        else:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            if run_id is not None:
                trace_repo.log(
                    run_id, node_name, "completed",
                    started_at, datetime.now(timezone.utc), duration_ms,
                    input_snapshot=state, output_snapshot=result, error=None,
                    batch_id=batch_id, thread_id=thread_id,
                )
            return result

    return wrapped
