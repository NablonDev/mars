from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from cmir_agent.domain.models import CMIR, EmailMessage


class EmailRepository(ABC):
    @abstractmethod
    def save(self, email: EmailMessage) -> int:
        ...

    @abstractmethod
    def update_extraction(self, email_id: int, cmir: CMIR) -> None:
        ...


class CMIRRepository(ABC):
    @abstractmethod
    def save(self, email_id: int, cmir: CMIR) -> int:
        ...


class ActionLogRepository(ABC):
    @abstractmethod
    def log(self, email_id: int, action: str, actor: str, details: Dict[str, Any]) -> None:
        ...
