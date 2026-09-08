"""Azure OpenAI chat client."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import LLMConfig


class AzureOpenAIConfigError(RuntimeError):
    """Raised when Azure OpenAI configuration is missing."""


class AzureOpenAIChatClient:
    """Chat client for Azure OpenAI through LangChain."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        if not (config.api_key and config.endpoint and config.deployment):
            raise AzureOpenAIConfigError(
                "Azure OpenAI is not configured. Set AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, "
                "and AZURE_OPENAI_DEPLOYMENT_NAME."
            )

        if timeout_seconds is None:
            timeout_seconds = config.timeout_seconds
        if max_retries is None:
            max_retries = config.max_retries

        self._model_name = config.deployment
        self._llm = ChatOpenAI(
            base_url=config.endpoint,
            api_key=SecretStr(config.api_key),
            model=config.deployment,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def model_name(self) -> str:
        """The configured Azure OpenAI deployment name, recorded alongside generated summaries."""
        return self._model_name

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        *,
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        """Send messages to the model and return its response.

        Tools bind for this call only, so one client serves both the tool-calling
        rounds and the final tool-free call of an agent loop.
        """
        llm: Any = self._llm
        if tools:
            llm = llm.bind_tools(tools)

        return llm.invoke(list(messages))
