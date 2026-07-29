from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional


class AgentRunRepository(ABC):
    """Tracks one row per graph run: is it running, paused, done, failed?"""

    @abstractmethod
    def start(self, thread_id: str) -> int:
        """Create a new run row, return its id."""
        ...

    @abstractmethod
    def update_status(
        self,
        run_id: int,
        status: str,
        *,
        email_id: Optional[int] = None,
        current_node: Optional[str] = None,
        error: Optional[str] = None,
        completed: bool = False,
    ) -> None:
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
    ) -> None:
        ...


class HITLActionRepository(ABC):
    """Audit trail of every human answer given at an interrupt point."""

    @abstractmethod
    def log(
        self,
        run_id: int,
        email_id: Optional[int],
        interrupt_type: str,
        question: Dict[str, Any],
        answer: Dict[str, Any],
        actor: str,
        decision: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        ...
