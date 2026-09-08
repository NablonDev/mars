"""Structured JSON logging with request IDs and sensitive-data redaction."""

from __future__ import annotations

import json
import logging
import logging.config
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime

REQUEST_ID_HEADER = "X-Request-ID"
UNKNOWN_REQUEST_ID = "-"

_request_id: ContextVar[str] = ContextVar(
    "request_id",
    default=UNKNOWN_REQUEST_ID,
)

_EXTRA_FIELDS = ("method", "path", "status", "duration_ms", "error_code")

_URL_CREDENTIALS = re.compile(r"([a-zA-Z][\w+.-]*://[^:/?#\s@]+:)[^@\s/]*(@)")

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(api[-_]?key|password|passwd|pwd|secret|token|authorization)"
    r"(?![A-Za-z0-9])"
    r"(\s*[=:]\s*)"
    r"""[^\s,;="'&)]+"""
)

_BEARER_TOKEN = re.compile(r"(?i)\b(bearer\s+)[\w\-._~+/]+=*")

_REDACTED = "***"


def get_request_id() -> str:
    """Return the request ID bound to the current context, or the unknown-request sentinel."""
    return _request_id.get()


def set_request_id(request_id: str) -> Token[str]:
    """Bind request_id to the current context and return a token for the matching reset."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    """Restore the context var to its value before the paired set_request_id call."""
    _request_id.reset(token)


def _redact(text: str) -> str:
    """Return text with bearer tokens, URL credentials, and secret-like assignments masked."""
    text = _BEARER_TOKEN.sub(rf"\1{_REDACTED}", text)
    text = _URL_CREDENTIALS.sub(rf"\1{_REDACTED}\2", text)
    return _SECRET_ASSIGNMENT.sub(rf"\1\2{_REDACTED}", text)


class RequestIdFilter(logging.Filter):
    """Add the current request ID to records that do not already have one."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Stamp the record with the current request ID if it doesn't already have one."""
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Render a log record as a single-line, redacted JSON object."""
        payload: dict[str, object] = {
            "request_id": getattr(record, "request_id", UNKNOWN_REQUEST_ID),
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=UTC,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
        }

        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = _redact(self.formatException(record.exc_info))
        elif record.exc_text:
            payload["exception"] = _redact(record.exc_text)

        return json.dumps(payload, default=str)


_configured = False


def configure_logging(level: str = "INFO", *, force: bool = False) -> None:
    """Set up structured JSON logging for the app and Uvicorn."""

    global _configured

    if _configured and not force:
        return

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_id": {"()": RequestIdFilter},
            },
            "formatters": {
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                    "filters": ["request_id"],
                }
            },
            "root": {
                "handlers": ["stdout"],
                "level": level,
            },
            "loggers": {
                "uvicorn": {
                    "handlers": ["stdout"],
                    "level": level,
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["stdout"],
                    "level": level,
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["stdout"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        }
    )

    _configured = True
