from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from cmir_agent.domain.models import CMIR, EmailMessage


class EmailRepository(ABC):
    @abstractmethod
    def save(self, email: EmailMessage) -> Any:
        ...

    @abstractmethod
    def save_for_queue(self, email: EmailMessage) -> Dict[str, Any]:
        """Persist an inbound email as queueable, returning the queue row."""
        ...

    @abstractmethod
    def claim_new_for_queue(self, limit: int) -> List[Dict[str, Any]]:
        """Claim new email rows in FIFO order for Service Bus enqueueing."""
        ...

    @abstractmethod
    def mark_queued(self, email_id: Any, queue_message_id: str) -> None:
        """Mark an email as queued after a successful Service Bus send."""
        ...

    @abstractmethod
    def mark_processing(self, email_id: Any, queue_message_id: Optional[str] = None) -> None:
        """Mark an email as being processed by the Service Bus consumer."""
        ...

    @abstractmethod
    def mark_queue_processed(self, email_id: Any) -> None:
        """Mark queue processing complete for an email."""
        ...

    @abstractmethod
    def mark_queue_failed(self, email_id: Any, error: str, *, retryable: bool = True) -> None:
        """Persist queue failure details and optionally make the row retryable."""
        ...

    @abstractmethod
    def get_queue_state(self, email_id: Any) -> Optional[Dict[str, Any]]:
        """Return queue lifecycle fields for one email row."""
        ...

    @abstractmethod
    def update_extraction(self, email_id: Any, cmir: CMIR) -> None:
        ...


class CMIRRepository(ABC):
    @abstractmethod
    def save(self, email_id: Any, cmir: CMIR) -> int:
        ...


class ActionLogRepository(ABC):
    @abstractmethod
    def log(self, email_id: Any, action: str, actor: str, details: Dict[str, Any]) -> None:
        ...
