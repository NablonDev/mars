"""Tests for `app.core.envelope`: `Envelope[T]`, `success_envelope`,
`error_envelope`. This is the `{success, message, data, error}` shape
`register_exception_handlers` builds every error response from (Phase 6);
Phase 7 wires route handlers to `success_envelope` too."""

from __future__ import annotations

from pydantic import BaseModel

from app.core.envelope import Envelope, ErrorBody, error_envelope, success_envelope


class _Widget(BaseModel):
    id: int
    name: str


def test_success_envelope_carries_data_and_no_error() -> None:
    envelope = success_envelope(_Widget(id=1, name="thing"), message="Widget fetched")

    assert envelope.success is True
    assert envelope.message == "Widget fetched"
    assert envelope.data == _Widget(id=1, name="thing")
    assert envelope.error is None


def test_success_envelope_defaults_message_to_ok() -> None:
    envelope = success_envelope({"a": 1})

    assert envelope.message == "OK"


def test_error_envelope_carries_error_and_no_data() -> None:
    envelope = error_envelope("PO_NOT_FOUND", "No such purchase order", details={"id": "abc"})

    assert envelope.success is False
    assert envelope.message == "No such purchase order"
    assert envelope.data is None
    assert envelope.error == ErrorBody(code="PO_NOT_FOUND", details={"id": "abc"})


def test_error_envelope_details_default_to_none() -> None:
    envelope = error_envelope("VALIDATION_ERROR", "bad input")

    assert envelope.error is not None
    assert envelope.error.details is None


def test_error_body_accepts_string_or_dict_details() -> None:
    assert ErrorBody(code="X", details="a plain string").details == "a plain string"
    assert ErrorBody(code="X", details={"k": "v"}).details == {"k": "v"}
    assert ErrorBody(code="X").details is None


def test_envelope_is_generic_and_serializes_typed_data() -> None:
    envelope: Envelope[_Widget] = Envelope(
        success=True, message="OK", data=_Widget(id=2, name="gizmo"), error=None
    )

    dumped = envelope.model_dump()
    assert dumped == {
        "success": True,
        "message": "OK",
        "data": {"id": 2, "name": "gizmo"},
        "error": None,
    }
