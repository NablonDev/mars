"""Tests for the X-Internal-Api-Key gate (app.api.dependencies.require_internal_api_key).

The `app`/`client` fixtures in tests/conftest.py override this dependency for
every other test in the suite, so the auth gate itself is never exercised
there. These tests are the one place it runs for real, against a live
FastAPI app with the override removed.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import require_internal_api_key
from app.core.config import Settings, get_settings
from tests.conftest import TEST_INTERNAL_API_KEY

HEADER_NAME = "X-Internal-Api-Key"
WRONG_KEY = "definitely-not-the-configured-key"


@pytest.fixture
def real_auth_client(app) -> TestClient:
    """A client with the global test override removed, so requests hit the
    real dependency instead of bypassing it."""
    del app.dependency_overrides[require_internal_api_key]
    return TestClient(app)


def test_missing_key_returns_401(real_auth_client: TestClient) -> None:
    resp = real_auth_client.get("/api/v1/orders")

    assert resp.status_code == 401


def test_wrong_key_returns_401(real_auth_client: TestClient) -> None:
    resp = real_auth_client.get("/api/v1/orders", headers={HEADER_NAME: WRONG_KEY})

    assert resp.status_code == 401


def test_correct_key_passes_through_to_the_real_handler(real_auth_client: TestClient) -> None:
    resp = real_auth_client.get("/api/v1/orders", headers={HEADER_NAME: TEST_INTERNAL_API_KEY})

    assert resp.status_code == 200
    assert resp.json() == []


def test_health_requires_no_key_at_all(real_auth_client: TestClient) -> None:
    resp = real_auth_client.get("/api/v1/health")

    assert resp.status_code == 200


def test_non_ascii_key_header_returns_401_not_500(real_auth_client: TestClient) -> None:
    """Regression test: a raw non-ASCII header value used to crash
    require_internal_api_key with an uncaught TypeError from
    secrets.compare_digest, surfacing as a 500 instead of a 401.

    httpx's `headers=` dict encodes str values as strict ASCII itself
    (raising before the request is even sent), so this passes the raw
    latin-1-encoded bytes directly -- exactly the wire form Starlette
    decodes real non-ASCII header bytes from -- to actually exercise the
    dependency rather than fail earlier in test setup.
    """
    resp = real_auth_client.get(
        "/api/v1/orders", headers=[(HEADER_NAME.encode("ascii"), "café-ñ-not-the-key".encode("latin-1"))]
    )

    assert resp.status_code == 401


def test_401_body_never_echoes_the_submitted_or_expected_key(real_auth_client: TestClient) -> None:
    resp = real_auth_client.get("/api/v1/orders", headers={HEADER_NAME: WRONG_KEY})

    assert resp.status_code == 401
    assert WRONG_KEY not in resp.text
    assert TEST_INTERNAL_API_KEY not in resp.text
    assert get_settings().internal_api_key not in resp.text


def test_missing_key_body_never_echoes_the_expected_key(real_auth_client: TestClient) -> None:
    resp = real_auth_client.get("/api/v1/orders")

    assert resp.status_code == 401
    assert TEST_INTERNAL_API_KEY not in resp.text
    assert get_settings().internal_api_key not in resp.text


def test_dependency_rejects_a_missing_header_directly() -> None:
    """A None header must 401, not raise TypeError out of
    secrets.compare_digest, which requires str/bytes, never None."""
    settings = get_settings()

    with pytest.raises(HTTPException) as exc_info:
        require_internal_api_key(x_internal_api_key=None, settings=settings)

    assert exc_info.value.status_code == 401


def test_dependency_accepts_the_configured_key_directly() -> None:
    settings = get_settings()

    assert require_internal_api_key(x_internal_api_key=settings.internal_api_key, settings=settings) is None


def test_dependency_rejects_a_non_ascii_key_directly() -> None:
    """secrets.compare_digest raises TypeError on a non-ASCII str; Starlette
    decodes raw header bytes as latin-1, so a non-ASCII header reaches the
    dependency as a valid (non-ASCII) str. This must 401, not propagate a
    TypeError into an uncaught 500."""
    settings = get_settings()

    with pytest.raises(HTTPException) as exc_info:
        require_internal_api_key(x_internal_api_key="café-ñ-not-the-key", settings=settings)

    assert exc_info.value.status_code == 401


def test_settings_fails_loudly_when_internal_api_key_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No default is allowed to silently accept requests: an absent
    INTERNAL_API_KEY must fail Settings construction, not fall back to
    something that would pass validation and disable the gate."""
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_rejects_a_blank_internal_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "   ")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_rejects_the_env_example_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh `cp .env.example .env` must fail startup, not silently run
    with a well-known, publicly-visible secret."""
    monkeypatch.setenv("INTERNAL_API_KEY", "replace-with-a-generated-secret")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_rejects_a_too_short_internal_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "short-but-not-blank")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_accepts_a_real_looking_internal_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "a-generated-secret-that-is-long-enough-1234")

    assert Settings(_env_file=None).internal_api_key == "a-generated-secret-that-is-long-enough-1234"
