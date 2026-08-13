"""Tests for AzureOpenAIChatClient. `ChatOpenAI` is mocked -- never a live call."""

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
        "azure_openai_endpoint": "https://example.openai.azure.com/openai/v1",
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


@patch("app.agents.providers.azure_openai.ChatOpenAI")
def test_constructor_passes_settings_through_to_chat_openai(mock_chat_openai: MagicMock):
    AzureOpenAIChatClient(_configured_settings(), timeout_seconds=42.0, max_retries=5)

    mock_chat_openai.assert_called_once_with(
        base_url="https://example.openai.azure.com/openai/v1",
        api_key=SecretStr("fake-key"),
        model="fake-deployment",
        timeout=42.0,
        max_retries=5,
    )


@patch("app.agents.providers.azure_openai.ChatOpenAI")
def test_constructor_defaults_match_module_constants(mock_chat_openai: MagicMock):
    client = AzureOpenAIChatClient(_configured_settings())

    assert client.model_name == "fake-deployment"
    mock_chat_openai.assert_called_once_with(
        base_url="https://example.openai.azure.com/openai/v1",
        api_key=SecretStr("fake-key"),
        model="fake-deployment",
        timeout=90.0,
        max_retries=2,
    )


@patch("app.agents.providers.azure_openai.ChatOpenAI")
def test_invoke_without_tools_skips_bind_tools(mock_chat_openai: MagicMock):
    mock_llm_instance = MagicMock()
    mock_chat_openai.return_value = mock_llm_instance
    mock_llm_instance.invoke.return_value = "response"

    client = AzureOpenAIChatClient(_configured_settings())
    result = client.invoke([])

    mock_llm_instance.bind_tools.assert_not_called()
    mock_llm_instance.invoke.assert_called_once_with([])
    assert result == "response"


@patch("app.agents.providers.azure_openai.ChatOpenAI")
def test_invoke_with_tools_binds_tools_before_invoking(mock_chat_openai: MagicMock):
    mock_llm_instance = MagicMock()
    mock_bound = MagicMock()
    mock_llm_instance.bind_tools.return_value = mock_bound
    mock_chat_openai.return_value = mock_llm_instance
    mock_bound.invoke.return_value = "response"

    tools = [MagicMock()]
    client = AzureOpenAIChatClient(_configured_settings())
    result = client.invoke([], tools=tools)

    mock_llm_instance.bind_tools.assert_called_once_with(tools)
    mock_bound.invoke.assert_called_once_with([])
    assert result == "response"
