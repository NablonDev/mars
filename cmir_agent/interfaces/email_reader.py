from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from cmir_agent.domain.models import EmailMessage


class EmailReader(ABC):
    """Port for fetching and acknowledging inbound emails."""

    @abstractmethod
    def fetch_unread(self) -> List[EmailMessage]:
        ...

    @abstractmethod
    def mark_as_read(self, imap_id: str) -> None:
        ...
