"""
Structured logging: one JSON line per event on stdout, every line
carrying the request id.

No new dependency -- `logging.config.dictConfig` plus a ~30-line
`Formatter` subclass covers what structlog/python-json-logger would be
pulled in for, and stays inspectable.

Two rules this module enforces so callers don't have to:

1. **Allowlisted fields only.** The formatter never dumps
   `record.__dict__`, so an `extra={...}` that happens to contain a
   credential can't silently end up in the log stream -- only the fields
   in `_EXTRA_FIELDS` are emitted.
2. **Redaction on the way out.** `_redact` scrubs URL credentials
   (`postgresql://user:pw@host` -- a real risk here, since SQLAlchemy and
   psycopg both put the DSN in some exception messages) and
   `key=value`-shaped secrets from the rendered message and from any
   traceback. Defense in depth, not a licence to log secrets: per the
   `error-handling-patterns` skill, log identifiers and metadata, not
   request bodies, prompts or completions.
"""

from __future__ import annotations

import json
import logging
import logging.config
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime

REQUEST_ID_HEADER = "X-Request-ID"
UNKNOWN_REQUEST_ID = "-"

# Module-level, so any code anywhere in a request's call stack can log with
# the right request id without it being threaded through every signature.
_request_id: ContextVar[str] = ContextVar("request_id", default=UNKNOWN_REQUEST_ID)

# The only `extra=` keys the formatter will emit. Anything else a caller
# passes is dropped on purpose -- see rule 1 in the module docstring.
_EXTRA_FIELDS = ("method", "path", "status", "duration_ms", "error_code")

# scheme://user:password@host -> scheme://user:***@host
_URL_CREDENTIALS = re.compile(r"([a-zA-Z][\w+.\-]*://[^:/?#\s@]+:)[^@\s/]*(@)")
# key=value / key: value for anything that names itself a secret.
# Deliberately not `\b...\b`: `_` is a word character, so `\b` finds no
# boundary between "_" and "PASSWORD" in `DB_PASSWORD=...` -- exactly this
# app's own env-var naming convention (`AZURE_OPENAI_API_KEY`,
# `DB_PASSWORD`) -- and the keyword silently never matches. The lookaround
# below treats anything that's *not* a letter/digit (including `_`/`-`) as
# a valid separator on both sides, so SCREAMING_SNAKE_CASE names redact
# correctly while "MYPASSWORDFIELD=x" (no real separator) still doesn't
# match, same as `\b` intended.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9])(api[-_]?key|password|passwd|pwd|secret|token|authorization)"
    r"(?![A-Za-z0-9])(\s*[=:]\s*)[^\s,;\"'&)]+"
)
# Authorization: Bearer <token>, where the value is a separate word
_BEARER_TOKEN = re.compile(r"(?i)\b(bearer\s+)[\w\-._~+/]+=*")

_REDACTED = "***"


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(request_id: str) -> Token[str]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


def _redact(text: str) -> str:
    # Bearer first: `_SECRET_ASSIGNMENT` would otherwise match
    # "Authorization: Bearer" and stop at the space, redacting the word
    # "Bearer" and leaving the token itself in the log.
    text = _BEARER_TOKEN.sub(rf"\1{_REDACTED}", text)
    text = _URL_CREDENTIALS.sub(rf"\1{_REDACTED}\2", text)
    return _SECRET_ASSIGNMENT.sub(rf"\1\2{_REDACTED}", text)


class RequestIdFilter(logging.Filter):
    """Stamps the current request id onto every record that doesn't
    already carry one. An explicit `extra={"request_id": ...}` wins --
    that's how the exception handlers log a 500 whose ContextVar has
    already been reset (see app/core/exceptions.py::_resolve_request_id)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line. `request_id` is the first key, so it's
    the first thing visible on any log line, including a 500's."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "request_id": getattr(record, "request_id", UNKNOWN_REQUEST_ID),
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
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
        # default=str so an unexpected non-serializable extra degrades to
        # its repr instead of raising inside the logging machinery.
        return json.dumps(payload, default=str)


_configured = False


def configure_logging(level: str = "INFO", *, force: bool = False) -> None:
    """Idempotent by design: `create_app()` is called once per process in
    production but once per test in the suite, and re-running `dictConfig`
    would tear pytest's own capture handler off the root logger mid-test.
    Pass `force=True` to reconfigure deliberately."""
    global _configured
    if _configured and not force:
        return

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": RequestIdFilter}},
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                    "filters": ["request_id"],
                }
            },
            "root": {"handlers": ["stdout"], "level": level},
            "loggers": {
                # Route uvicorn's own output through the same JSON handler
                # instead of its default coloured text formatter.
                "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
                "uvicorn.error": {"handlers": ["stdout"], "level": level, "propagate": False},
                # Silenced, not reformatted: app/core/middleware/logging.py
                # emits the access line (with the request id), and two
                # access logs per request is noise, not redundancy.
                "uvicorn.access": {"handlers": ["stdout"], "level": "WARNING", "propagate": False},
            },
        }
    )
    _configured = True
