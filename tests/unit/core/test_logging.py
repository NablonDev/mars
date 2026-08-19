"""
The JSON formatter, the request-id filter, and the redaction pass -- unit
tests against `logging.LogRecord` directly, so nothing here depends on
`dictConfig` having run or on pytest's own capture handlers.
"""

import json
import logging

from app.core.logging import (
    UNKNOWN_REQUEST_ID,
    JsonFormatter,
    RequestIdFilter,
    get_request_id,
    reset_request_id,
    set_request_id,
)


def _record(message: str, *, args=(), exc_info=None, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=exc_info,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _formatted(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


def test_every_line_is_one_json_object_with_the_request_id_first():
    line = JsonFormatter().format(_record("hello", request_id="req-1"))

    assert "\n" not in line
    payload = json.loads(line)
    assert next(iter(payload)) == "request_id"
    assert payload["request_id"] == "req-1"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello"
    assert payload["timestamp"].endswith("+00:00")


def test_allowlisted_extras_are_emitted():
    payload = _formatted(
        _record("GET /x 200", method="GET", path="/x", status=200, duration_ms=1.5, error_code="NOPE")
    )

    assert payload["method"] == "GET"
    assert payload["path"] == "/x"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 1.5
    assert payload["error_code"] == "NOPE"


def test_non_allowlisted_extras_are_dropped():
    """The formatter never dumps `record.__dict__`, so an `extra=` that
    happens to carry a credential can't reach the log stream by accident."""
    payload = _formatted(_record("done", api_key="s3cret", request_body={"password": "hunter2"}))

    assert "api_key" not in payload
    assert "request_body" not in payload
    assert "s3cret" not in json.dumps(payload)


def test_url_credentials_are_redacted_from_the_message():
    payload = _formatted(_record("could not connect to postgresql://svc:sup3rs3cret@db.internal:5432/mars"))

    assert "sup3rs3cret" not in payload["message"]
    assert "postgresql://svc:***@db.internal:5432/mars" in payload["message"]


def test_secret_assignments_are_redacted_from_the_message():
    payload = _formatted(_record("call failed: api-key=abc123def, password: hunter2, token=zzz"))

    assert "abc123def" not in payload["message"]
    assert "hunter2" not in payload["message"]
    assert "zzz" not in payload["message"]
    assert payload["message"].count("***") == 3


def test_secret_assignments_with_underscore_prefixed_names_are_redacted():
    """Regression test: `\\b...\\b` alone never matches inside
    `DB_PASSWORD=...` because `_` is a word character, so there's no
    boundary between "_" and "PASSWORD" -- this app's own env-var naming
    convention (`AZURE_OPENAI_API_KEY`, `DB_PASSWORD`) was exactly the
    case that leaked a real secret unredacted."""
    payload = _formatted(_record("startup failed: DB_PASSWORD=SuperSecret123"))

    assert "SuperSecret123" not in payload["message"]
    assert "***" in payload["message"]

    payload = _formatted(_record("azure call failed: AZURE_OPENAI_API_KEY=abc123def"))

    assert "abc123def" not in payload["message"]
    assert "***" in payload["message"]


def test_bearer_tokens_are_redacted_from_the_message():
    payload = _formatted(_record("upstream rejected Authorization: Bearer eyJhbGciOi.J9.abc-_123"))

    assert "eyJhbGciOi" not in payload["message"]


def test_redaction_applies_to_interpolated_args():
    payload = _formatted(_record("dsn=%s", args=("postgresql://svc:sup3rs3cret@db/mars",)))

    assert "sup3rs3cret" not in payload["message"]


def test_tracebacks_are_included_but_redacted():
    try:
        raise RuntimeError("bad dsn postgresql://svc:sup3rs3cret@db.internal/mars")
    except RuntimeError:
        import sys

        payload = _formatted(_record("boom", exc_info=sys.exc_info()))

    assert "Traceback" in payload["exception"]
    assert "RuntimeError" in payload["exception"]
    assert "sup3rs3cret" not in payload["exception"]


def test_filter_stamps_the_current_request_id():
    token = set_request_id("req-from-contextvar")
    try:
        record = _record("hello")
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "req-from-contextvar"
    finally:
        reset_request_id(token)

    assert get_request_id() == UNKNOWN_REQUEST_ID


def test_filter_does_not_overwrite_an_explicit_request_id():
    """The exception handlers pass the id explicitly, because a 500 is
    handled outside the middleware that owns the ContextVar."""
    token = set_request_id("from-contextvar")
    try:
        record = _record("hello", request_id="explicit")
        RequestIdFilter().filter(record)
    finally:
        reset_request_id(token)

    assert record.request_id == "explicit"


def test_records_without_a_request_id_fall_back_to_a_placeholder():
    payload = _formatted(_record("startup, outside any request"))

    assert payload["request_id"] == UNKNOWN_REQUEST_ID
