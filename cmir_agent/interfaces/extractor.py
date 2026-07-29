from __future__ import annotations

from abc import ABC, abstractmethod

from cmir_agent.domain.models import CMIR


class CMIRExtractor(ABC):
    """Port for turning raw email text into a structured CMIR draft."""

    @abstractmethod
    def extract(self, body: str) -> CMIR:
        ...
