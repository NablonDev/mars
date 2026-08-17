"""Application-level errors that map cleanly to the PRD API error contract."""
from __future__ import annotations

from typing import Any, Dict, Optional


class ServiceError(Exception):
    """Application error that maps cleanly to the PRD error contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
