"""Tests for app/workers/handlers.py -- the single per-item execution
entry point.

Runs against the real (SQLite, in-memory) `database` fixture with real
ProjectionService/FineSummaryService, since `execute_job` constructs both
directly from a fresh session -- only the LLM call is faked
(`FakeChatClient`, the same duck-typed shape `tests/services/
test_fine_summary.py`'s fake uses). No live API call anywhere here.

Seeds via `database.session()` directly (auto-committing, like
`scripts/run_projection_cli.py`'s own test in
`tests/test_run_projection_cli.py`) rather than the `services`/`db_session`
fixtures, which hold one long-lived, uncommitted session open for the
whole test -- `execute_job` opens and closes its own sessions, and on the
single shared SQLite connection the `database` fixture uses, mixing an
open uncommitted session with `execute_job`'s own sessions would make
inserts invisible across sessions.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from uuid import uuid4

import pytest

from app.agents.prompts.fine_summary.v3 import PROMPT_VERSION
from app.core.config import Settings
from app.core.exceptions import NoActiveRulesError, NoProjectionExistsError, OrderNotFoundError
from app.db.session import Database
from app.queue.types import ClaimedJob
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.master_data import MasterDataRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.workers.handlers import execute_job


class _FakeAIMessage:
    def __init__(self, content: str | None, tool_calls: list[dict]) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeChatClient:
    """Always answers immediately with no tool calls. `model_name`/
    `invoke(messages, *, tools=None)` is the only shape
    `FineSummaryService` needs -- no live Azure OpenAI call."""

    model_name = "fake-model"

    def __init__(self, final_content: str | None = "All clear.") -> None:
        self.final_content = final_content
        self.invocations: list[dict] = []

    def invoke(self, messages, *, tools=None):
        self.invocations.append({"messages": messages, "tools": tools})
        return _FakeAIMessage(content=self.final_content, tool_calls=[])


def _make_job(
    order_id: str,
    projection_date: date,
    task_type: str,
    *,
    stacking_mode_override: str | None = None,
    force_regenerate_summary: bool = False,
) -> ClaimedJob:
    return ClaimedJob(
        job_item_id=uuid4(),
        job_run_id=uuid4(),
        order_id=order_id,
        projection_date=projection_date,
        task_type=task_type,
        stacking_mode_override=stacking_mode_override,
        force_regenerate_summary=force_regenerate_summary,
        attempt_count=1,
        max_attempts=5,
    )


def _seed_order(
    database: Database,
    order_id: str = "ORD-WORKER",
    *,
    with_rules: bool = True,
) -> None:
    with database.session() as session:
        master_data = MasterDataRepository(session)
        rules = FineRuleRepository(session)
        orders = OrderRepository(session)

        retailer_id = f"RET-{order_id}"
        master_data.add_retailer(retailer_id, f"Retailer {order_id}", None, "SUM")
        master_data.add_sku(f"SKU-{order_id}", f"MAT-{order_id}", None)
        master_data.add_location(f"LOC-{order_id}", None, None)
        orders.create_order(
            order_id=order_id,
            retailer_id=retailer_id,
            sku_id=f"SKU-{order_id}",
            ship_from_location_id=f"LOC-{order_id}",
            order_qty=100,
            unit_price=5.0,
            order_date=date(2026, 8, 1),
            requested_delivery_date=date(2026, 8, 10),
            required_ship_date=date(2026, 8, 8),
        )
        if with_rules:
            rules.add_rule(
                rule_id=f"RULE-{order_id}-FLAT",
                retailer_id=retailer_id,
                violation_type="OTIF_LATE",
                calc_type="FLAT_FEE",
                rate=50.0,
            )


def test_order_run_runs_projection_then_schedules_and_generates_summary(database):
    _seed_order(database, "ORD-A")
    llm = FakeChatClient()
    job = _make_job("ORD-A", date(2026, 8, 5), "ORDER_RUN")

    execute_job(job, database, Settings(), llm, heartbeat=None)

    with database.session() as session:
        history = ProjectionRepository(session).get_history("ORD-A")
        summary_row = FineSummaryRepository(session).get_by_key("ORD-A", date(2026, 8, 5), PROMPT_VERSION)

    assert history, "projection should have been persisted"
    assert summary_row is not None
    assert summary_row["status"] == "READY"
    assert summary_row["summary"] == "All clear."
    assert len(llm.invocations) >= 1


def test_summary_regen_only_generates_summary_no_new_projection(database):
    _seed_order(database, "ORD-B")
    llm = FakeChatClient()

    # ORDER_RUN first so a projection exists (SUMMARY_REGEN's precondition).
    execute_job(_make_job("ORD-B", date(2026, 8, 5), "ORDER_RUN"), database, Settings(), llm, heartbeat=None)

    with database.session() as session:
        history_before = ProjectionRepository(session).get_history("ORD-B")

    # Force regeneration on the same date; SUMMARY_REGEN must not touch
    # the projection history.
    regen_job = _make_job("ORD-B", date(2026, 8, 5), "SUMMARY_REGEN", force_regenerate_summary=True)
    execute_job(regen_job, database, Settings(), llm, heartbeat=None)

    with database.session() as session:
        history_after = ProjectionRepository(session).get_history("ORD-B")
        summary_row = FineSummaryRepository(session).get_by_key("ORD-B", date(2026, 8, 5), PROMPT_VERSION)

    assert history_after == history_before
    assert summary_row is not None
    assert summary_row["status"] == "READY"
    # One LLM round-trip pair (round + final) per generation -- forced
    # regeneration means a second generation actually ran.
    assert len(llm.invocations) == 4


def test_summary_regen_without_a_projection_raises_no_projection_exists(database):
    _seed_order(database, "ORD-NOPROJ")
    llm = FakeChatClient()
    job = _make_job("ORD-NOPROJ", date(2026, 8, 5), "SUMMARY_REGEN")

    with pytest.raises(NoProjectionExistsError):
        execute_job(job, database, Settings(), llm, heartbeat=None)


def test_order_run_propagates_order_not_found(database):
    llm = FakeChatClient()
    job = _make_job("ORD-DOES-NOT-EXIST", date(2026, 8, 5), "ORDER_RUN")

    with pytest.raises(OrderNotFoundError):
        execute_job(job, database, Settings(), llm, heartbeat=None)


def test_order_run_propagates_no_active_rules(database):
    _seed_order(database, "ORD-NORULES", with_rules=False)
    llm = FakeChatClient()
    job = _make_job("ORD-NORULES", date(2026, 8, 5), "ORDER_RUN")

    with pytest.raises(NoActiveRulesError):
        execute_job(job, database, Settings(), llm, heartbeat=None)


def test_unknown_task_type_raises_value_error(database):
    _seed_order(database, "ORD-C")
    llm = FakeChatClient()
    job = _make_job("ORD-C", date(2026, 8, 5), "BOGUS_TASK_TYPE")

    with pytest.raises(ValueError, match="Unknown task_type"):
        execute_job(job, database, Settings(), llm, heartbeat=None)


def test_heartbeat_invoked_once_per_tool_round(database):
    """FakeChatClient never returns tool_calls, so the loop breaks after
    round 1 -- exactly one heartbeat call (see `_run_tool_loop`)."""
    _seed_order(database, "ORD-D")
    llm = FakeChatClient()
    job = _make_job("ORD-D", date(2026, 8, 5), "ORDER_RUN")
    heartbeat_calls = []

    execute_job(job, database, Settings(), llm, heartbeat=lambda: heartbeat_calls.append(1))

    assert len(heartbeat_calls) == 1


def test_session_is_not_held_across_the_llm_call(database):
    """Regression guard for the documented session-lifetime contract:
    the projection phase and the summary phase must each get their own
    session, never one held open across `llm.invoke`."""
    _seed_order(database, "ORD-E")
    # Strong references to the actual Session objects, not `id(session)` --
    # once a session's `with` block exits it can be garbage-collected, and
    # CPython is free to hand a *new* object the exact same id(), which
    # would make an id()-based comparison here flaky (occasionally "equal"
    # by pure memory-address reuse, not by the two sessions actually being
    # the same object).
    seen_sessions: list[object] = []
    real_session_cm = database.session

    @contextmanager
    def _tracking_session():
        with real_session_cm() as session:
            seen_sessions.append(session)
            yield session

    database.session = _tracking_session  # type: ignore[method-assign]
    try:
        llm = FakeChatClient()
        job = _make_job("ORD-E", date(2026, 8, 5), "ORDER_RUN")
        execute_job(job, database, Settings(), llm, heartbeat=None)
    finally:
        database.session = real_session_cm  # type: ignore[method-assign]

    # One session for the projection phase, one fresh session for the
    # summary phase -- never the same session object reused across both.
    assert len(seen_sessions) == 2
    assert seen_sessions[0] is not seen_sessions[1]
