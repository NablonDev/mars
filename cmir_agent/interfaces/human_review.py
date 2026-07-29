from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict


class HumanReviewPort(ABC):
    """Port for turning an interrupt payload into a human's answer.

    Kept separate from the LangGraph nodes so the pause/resume mechanism
    (interrupt / Command) stays UI-agnostic: today this is a CLI prompt,
    tomorrow it could be a web endpoint (the "Workbench") feeding the same
    graph via Command(resume=...), with zero changes to workflow code.
    """

    @abstractmethod
    def request_missing_fields(self, payload: Dict) -> Dict[str, str]:
        ...

    @abstractmethod
    def request_approval(self, payload: Dict) -> Dict[str, str]:
        ...
