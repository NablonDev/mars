"""Protocols separating durable job state from dispatch."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.queue.types import ClaimedJob


class JobDispatcher(Protocol):
    """Dispatch notifications for durable job items."""

    def dispatch(self, job_item_id: UUID, *, delay_seconds: int = 0) -> None:
        """Schedule a job item for immediate or delayed delivery."""
        ...

    def close(self) -> None:
        """Release backend resources.

        Must be safe to call more than once. Backends without persistent
        resources may implement this as a no-op.
        """
        ...


class JobSource(Protocol):
    """Claim and settle durable job items."""

    def claim_batch(self, worker_id: str, limit: int) -> list[ClaimedJob]:
        """Atomically claim eligible items for `worker_id`.

        A job item may be claimed by at most one worker at a time. Transport
        redelivery must not result in duplicate execution.
        """
        ...

    def heartbeat(self, job: ClaimedJob, worker_id: str) -> bool:
        """Extend ownership of a claimed item.

        Returns False if ownership can no longer be established. Callers
        must stop processing the item when ownership is lost.
        """
        ...

    def ack(self, job: ClaimedJob, worker_id: str) -> None:
        """Mark a successfully completed item as terminal."""
        ...

    def nack(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        error: str,
        error_code: str,
        retry_in_seconds: int,
    ) -> None:
        """Return a retryable failure to PENDING or move it to DEAD.

        The implementation decides based on the item's retry budget.
        """
        ...

    def dead_letter(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        error: str,
        error_code: str,
    ) -> None:
        """Mark a non-retryable failure as DEAD."""
        ...

    def release(self, job: ClaimedJob, worker_id: str) -> None:
        """Return a claimed item to PENDING without consuming an attempt."""
        ...

    def reclaim_stale(self, visibility_timeout_seconds: int) -> int:
        """Return abandoned claims to PENDING and report how many were reset."""
        ...
    