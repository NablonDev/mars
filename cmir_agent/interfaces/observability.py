from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional

from cmir_agent.domain.models import PendingHumanAction, WorkflowThread


class AgentRunRepository(ABC):
    """Tracks one independent email agent run grouped by batch_id."""

    @abstractmethod
    def start(
        self,
        *,
        batch_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        email_id: Optional[Any] = None,
    ) -> int:
        """Create one per-email agent run row and return its id."""
        ...

    @abstractmethod
    def update_status(
        self,
        run_id: int,
        status: str,
        *,
        email_id: Optional[Any] = None,
        current_node: Optional[str] = None,
        error: Optional[str] = None,
        completed: bool = False,
    ) -> None:
        ...

    @abstractmethod
    def update_thread_counts(
        self,
        run_id: int,
        *,
        total_threads: Optional[int] = None,
        completed_threads: Optional[int] = None,
        waiting_threads: Optional[int] = None,
        failed_threads: Optional[int] = None,
    ) -> None:
        """Legacy counter hook retained for compatibility; batch rollups are grouped by batch_id."""
        ...

    @abstractmethod
    def list_batches(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> tuple[list[Dict[str, Any]], Optional[str]]:
        """Return batch-run summaries for operations monitoring."""
        ...

    @abstractmethod
    def list_agents(
        self,
        *,
        batch_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> tuple[list[Dict[str, Any]], Optional[str]]:
        """Return per-email agent run summaries."""
        ...


class WorkflowThreadRepository(ABC):
    """Persists one UI-facing workflow thread per source email."""

    @abstractmethod
    def create(self, thread: WorkflowThread) -> str:
        """Create a workflow thread and return its thread_id."""
        ...

    @abstractmethod
    def update_status(
        self,
        thread_id: str,
        *,
        status: str,
        stage: str,
        current_node: Optional[str] = None,
        cmir_status: Optional[str] = None,
        latest_snapshot: Optional[Dict[str, Any]] = None,
        pending_action_id: Optional[int] = None,
        error: Optional[str] = None,
        completed: bool = False,
    ) -> None:
        """Persist the latest workflow state for a review thread."""
        ...

    @abstractmethod
    def update_if_current(
        self,
        thread_id: str,
        *,
        expected_updated_at: str,
        status: str,
        stage: str,
        current_node: Optional[str] = None,
        cmir_status: Optional[str] = None,
        latest_snapshot: Optional[Dict[str, Any]] = None,
        pending_action_id: Optional[int] = None,
        error: Optional[str] = None,
        completed: bool = False,
    ) -> bool:
        """Update only when optimistic concurrency timestamp still matches."""
        ...

    @abstractmethod
    def get_by_thread_id(self, thread_id: str) -> Optional[WorkflowThread]:
        """Return one workflow thread, or None when it does not exist."""
        ...

    @abstractmethod
    def get_by_email_id(self, email_id: Any) -> Optional[WorkflowThread]:
        """Return the latest workflow thread for one email row, if any."""
        ...

    @abstractmethod
    def list_threads(
        self,
        *,
        batch_id: Optional[str] = None,
        agent_run_id: Optional[int] = None,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        sender: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> tuple[list[Dict[str, Any]], Optional[str]]:
        """Return reviewable workflow rows for the UI queue."""
        ...

    @abstractmethod
    def get_stage(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Return the current stage payload for one workflow thread."""
        ...

    @abstractmethod
    def get_snapshot(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Return one workflow thread with email, CMIR, and history data."""
        ...


class PendingHumanActionRepository(ABC):
    """Persists open and completed human-review interrupts by thread_id."""

    @abstractmethod
    def create_open(self, action: PendingHumanAction) -> int:
        """Create one open pending action for a workflow thread."""
        ...

    @abstractmethod
    def complete(
        self,
        action_id: int,
        *,
        answer: Dict[str, Any],
        actor: str,
    ) -> None:
        """Mark an open pending action as completed with the reviewer's answer."""
        ...

    @abstractmethod
    def get_open_for_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Return the open pending action for a thread, if one exists."""
        ...


class HITLStateRepository(ABC):
    """Atomically persists reviewer actions and resulting thread state transitions."""

    @abstractmethod
    def apply_human_action(
        self,
        *,
        run_id: int,
        batch_id: Optional[str],
        thread_id: str,
        email_id: str,
        pending_action_id: int,
        interrupt_type: str,
        question: Dict[str, Any],
        answer: Dict[str, Any],
        actor: str,
        action_type: str,
        next_status: str,
        next_stage: str,
        next_current_node: Optional[str] = None,
        next_cmir_status: Optional[str] = None,
        next_latest_snapshot: Optional[Dict[str, Any]] = None,
        next_pending_interrupt_type: Optional[str] = None,
        next_pending_payload: Optional[Dict[str, Any]] = None,
        next_pending_state_snapshot: Optional[Dict[str, Any]] = None,
        decision: Optional[str] = None,
        reason: Optional[str] = None,
        field_changes: Optional[Dict[str, Any]] = None,
        completed: bool = False,
    ) -> Optional[int]:
        """Persist one reviewer action and the resulting thread state in a single transaction."""
        ...


class AgentTraceRepository(ABC):
    """One row per node execution - completed, paused on interrupt, or failed."""

    @abstractmethod
    def log(
        self,
        run_id: int,
        node_name: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: int,
        input_snapshot: Optional[Dict[str, Any]],
        output_snapshot: Optional[Dict[str, Any]],
        error: Optional[str],
        batch_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> None:
        ...


class HITLActionRepository(ABC):
    """Audit trail of every human answer given at an interrupt point."""

    @abstractmethod
    def log(
        self,
        run_id: int,
        email_id: Optional[Any],
        interrupt_type: str,
        question: Dict[str, Any],
        answer: Dict[str, Any],
        actor: str,
        decision: Optional[str] = None,
        reason: Optional[str] = None,
        batch_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        action_type: Optional[str] = None,
        field_changes: Optional[Dict[str, Any]] = None,
    ) -> None:
        ...
