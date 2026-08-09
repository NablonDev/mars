from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from cmir_agent.domain.models import EmailMessage


class EmailReader(ABC):
    """Port for fetching and acknowledging inbound emails."""

    @abstractmethod
    def fetch_unread(
        self,
        *,
        limit: Optional[int] = None,
        subject_contains: Optional[str] = None,
        unread_only: bool = True,
    ) -> List[EmailMessage]:
        ...

    @abstractmethod
    def mark_as_read(self, imap_id: str) -> None:
        ...
