"""Azure OpenAI chat client."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import Settings


class AzureOpenAIConfigError(RuntimeError):
    """Raised when Azure OpenAI configuration is missing."""


class AzureOpenAIChatClient:
    """Chat client for Azure OpenAI through LangChain."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        if not (
            settings.azure_openai_api_key
            and settings.azure_openai_endpoint
            and settings.azure_openai_deployment_name
        ):
            raise AzureOpenAIConfigError(
                "Azure OpenAI is not configured -- set "
                "AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, and "
                "AZURE_OPENAI_DEPLOYMENT_NAME."
            )

        if timeout_seconds is None:
            timeout_seconds = settings.azure_openai_timeout_seconds
        if max_retries is None:
            max_retries = settings.azure_openai_max_attempts

        self._model_name = settings.azure_openai_deployment_name
        self._llm = ChatOpenAI(
            base_url=settings.azure_openai_endpoint,
            api_key=SecretStr(settings.azure_openai_api_key),
            model=settings.azure_openai_deployment_name,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        *,
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        llm: Any = self._llm
        if tools:
            llm = llm.bind_tools(tools)

        return llm.invoke(list(messages))
