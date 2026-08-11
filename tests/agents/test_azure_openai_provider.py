"""
Tests for AzureOpenAIChatClient (app/agents/providers/azure_openai.py).
`AzureChatOpenAI` is mocked at the class level everywhere -- never a live
call to Azure OpenAI. Covers: config-error-at-construction-time-if-unset,
deployment/timeout params passed through, and retry-on-exception behavior.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from app.agents.providers.azure_openai import (
    AzureOpenAIChatClient,
    AzureOpenAIConfigError,
)
from app.core.config import Settings


def _configured_settings(**overrides) -> Settings:
    defaults = {
        "azure_openai_api_key": "fake-key",
        "azure_openai_endpoint": "https://example.openai.azure.com/",
        "azure_openai_api_version": "2024-08-01-preview",
        "azure_openai_deployment_name": "fake-deployment",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_raises_clear_error_if_azure_openai_not_configured():
    settings = Settings(
        azure_openai_api_key="",
        azure_openai_endpoint="",
        azure_openai_deployment_name="",
    )
    with pytest.raises(AzureOpenAIConfigError):
        AzureOpenAIChatClient(settings)


@patch("app.agents.providers.azure_openai.AzureChatOpenAI")
def test_deployment_and_timeout_params_passed_through(mock_azure_chat_openai: MagicMock):
    settings = _configured_settings()

    AzureOpenAIChatClient(settings, timeout_seconds=42.0)

    mock_azure_chat_openai.assert_called_once_with(
        azure_endpoint="https://example.openai.azure.com/",
        api_key=SecretStr("fake-key"),
        api_version="2024-08-01-preview",
        azure_deployment="fake-deployment",
        timeout=42.0,
        # Disabled deliberately -- see the constructor's own comment.
        # `_invoke_with_retry` below must be the *only* retry authority,
        # or every one of its "attempts" can silently balloon into
        # multiple real HTTP calls hidden inside the SDK, each with its
        # own backoff -- found live against a real, slow deployment.
        max_retries=0,
    )


@patch("app.agents.providers.azure_openai.AzureChatOpenAI")
def test_call_with_tools_retries_on_exception_then_succeeds(mock_azure_chat_openai: MagicMock):
    mock_llm_instance = MagicMock()
    mock_bound = MagicMock()
    mock_llm_instance.bind_tools.return_value = mock_bound
    mock_azure_chat_openai.return_value = mock_llm_instance

    success_result = MagicMock()
    success_result.tool_calls = []
    success_result.content = "final answer"
    mock_bound.invoke.side_effect = [RuntimeError("transient failure"), success_result]

    client = AzureOpenAIChatClient(_configured_settings(), max_attempts=3, backoff_seconds=0.0)
    result = client.call_with_tools(
        "system prompt",
        [{"role": "user", "content": "<DATA>{}</DATA>"}],
        tools=[{"type": "function", "function": {"name": "some_tool"}}],
    )

    assert mock_bound.invoke.call_count == 2
    assert result.content == "final answer"
    assert result.tool_calls == []


@patch("app.agents.providers.azure_openai.AzureChatOpenAI")
def test_call_with_tools_raises_after_exhausting_all_attempts(mock_azure_chat_openai: MagicMock):
    mock_llm_instance = MagicMock()
    mock_llm_instance.bind_tools.return_value = mock_llm_instance
    mock_azure_chat_openai.return_value = mock_llm_instance
    mock_llm_instance.invoke.side_effect = RuntimeError("always fails")

    client = AzureOpenAIChatClient(_configured_settings(), max_attempts=3, backoff_seconds=0.0)

    with pytest.raises(RuntimeError, match="always fails"):
        client.call_with_tools("system prompt", [], tools=[{"type": "function", "function": {}}])

    assert mock_llm_instance.invoke.call_count == 3


@patch("app.agents.providers.azure_openai.AzureChatOpenAI")
def test_retry_warning_names_the_actual_exception_type(mock_azure_chat_openai: MagicMock, caplog):
    """Regression test: a user reported the logs showing nothing but the
    SDK's own uninformative "Retrying request to ... in ... seconds"
    lines with no indication of *why* -- because the real reason (status
    code, timeout, connection error) is a openai SDK DEBUG-level log this
    app doesn't (and, for secret-leakage reasons, shouldn't) turn on.
    This app's own warning is the fix, but only if it actually names the
    real exception type, not just `str(exc)` (which for some exception
    types is uninformative on its own, e.g. a bare `RuntimeError`)."""
    mock_llm_instance = MagicMock()
    mock_llm_instance.bind_tools.return_value = mock_llm_instance
    mock_azure_chat_openai.return_value = mock_llm_instance
    mock_llm_instance.invoke.side_effect = [
        TimeoutError("Request timed out."),
        MagicMock(tool_calls=[], content="ok"),
    ]

    client = AzureOpenAIChatClient(_configured_settings(), max_attempts=3, backoff_seconds=0.0)
    with caplog.at_level("WARNING", logger="app.agents.providers.azure_openai"):
        client.call_with_tools("system prompt", [], tools=[{"type": "function", "function": {}}])

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "attempt 1/3" in message
    assert "TimeoutError" in message
    assert "Request timed out." in message


@patch("app.agents.providers.azure_openai.AzureChatOpenAI")
def test_call_structured_returns_schema_instance(mock_azure_chat_openai: MagicMock):
    from pydantic import BaseModel

    class Out(BaseModel):
        headline: str

    mock_llm_instance = MagicMock()
    mock_structured = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_structured
    mock_azure_chat_openai.return_value = mock_llm_instance
    mock_structured.invoke.return_value = Out(headline="hi")

    client = AzureOpenAIChatClient(_configured_settings())
    result = client.call_structured("system prompt", [{"role": "user", "content": "<DATA>{}</DATA>"}], Out)

    assert isinstance(result, Out)
    assert result.headline == "hi"
    mock_llm_instance.with_structured_output.assert_called_once_with(Out)
