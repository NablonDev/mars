"""
Wraps `langchain_openai.AzureChatOpenAI` with retries, backoff, and a
timeout -- no service in this codebase calls the SDK directly.

This is deliberately NOT the pattern in the sibling cmir_agent project's
`infrastructure/azure_openai.py`: that file calls `.invoke()` with no
retry or timeout at all, and splices the raw email body straight into
one undelimited prompt string with no system/user separation. Here,
every piece of retrieved/tool content goes into a delimited `<DATA>`
block in a user or tool message (built by the caller -- see
app/services/explanation_service.py), never into the system prompt.

`.bind_tools()` and `.with_structured_output()` do technically compose
into one Runnable on the pinned langchain-openai/langchain-core versions
(`bound.with_structured_output(...)` returns a valid RunnableSequence),
but not the other way around, and relying on that composition to also
correctly disable tool-calling for the final forced-structured-output
round is not something to depend on implicitly. Instead this client
exposes two separate calls -- `call_with_tools` (tools bound, free-form
response) for the tool-calling rounds, and `call_structured` (a fresh
`with_structured_output` binding, no tools) for the final round -- so the
"tools disabled" guarantee comes from which method is called, not from
inference about Runnable composition.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, SecretStr

from app.agents.base import ChatTurnResult, ToolCall
from app.core.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 30.0


class AzureOpenAIConfigError(RuntimeError):
    """Raised when a real Azure OpenAI call is attempted but the required
    settings are unset. Deliberately not raised at Settings-construction
    time -- the Azure OpenAI settings default to empty strings precisely
    so the rest of the app (health check, unrelated tests) doesn't break
    in environments without Azure credentials. This is the "fail loudly
    at call time, not import time" boundary."""


class AzureOpenAIChatClient:
    """Implements `app.agents.base.StructuredChatClient`."""

    def __init__(
        self,
        settings: Settings,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not (
            settings.azure_openai_api_key
            and settings.azure_openai_endpoint
            and settings.azure_openai_deployment_name
        ):
            raise AzureOpenAIConfigError(
                "Azure OpenAI is not configured -- set AZURE_OPENAI_API_KEY, "
                "AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_DEPLOYMENT_NAME to "
                "use the explanation service."
            )
        self._model_name = settings.azure_openai_deployment_name
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._llm = AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=SecretStr(settings.azure_openai_api_key),
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_deployment_name,
            timeout=timeout_seconds,
            # The underlying openai SDK client retries transient failures
            # on its own by default (2 retries, its own backoff) --
            # *underneath* `_invoke_with_retry` below, invisibly to it.
            # That means every one of our own "attempts" could silently
            # balloon into up to 3 real HTTP calls with their own backoff
            # sleep in between, multiplying total wall-clock time far
            # past what `max_attempts`/`backoff_seconds` promise, and it's
            # why a slow deployment could look "stuck retrying" for
            # minutes with nothing informative in the logs: the SDK's own
            # retry logging (`openai._base_client`) only states the
            # *reason* (status code, timeout, connection error) at DEBUG,
            # never at INFO -- turning that logger up would also risk
            # dumping request/response bodies that can carry the API key.
            # Disabling it here makes `_invoke_with_retry` the one and
            # only retry authority: every real HTTP attempt is now exactly
            # one of `self._max_attempts`, and its own warning log (which
            # does carry the real exception) fires immediately on each one
            # that fails, not only after the SDK's hidden retries are
            # already exhausted.
            max_retries=0,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    def _invoke_with_retry(self, runnable: Any, lc_messages: list[BaseMessage]) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return runnable.invoke(lc_messages)
            except Exception as exc:  # noqa: BLE001 -- a retry wrapper must catch broadly
                last_exc = exc
                # `type(exc).__name__` first, unconditionally -- so
                # "Request timed out." (openai.APITimeoutError's entire
                # str()) reads as a timeout and not, say, a connection
                # error or a 400, without needing to go dig through a
                # traceback to find out which.
                logger.warning(
                    "Azure OpenAI call failed (attempt %s/%s): %s: %s",
                    attempt,
                    self._max_attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self._max_attempts:
                    time.sleep(self._backoff_seconds * (2 ** (attempt - 1)))
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _to_lc_messages(system_prompt: str, messages: list[dict[str, Any]]) -> list[BaseMessage]:
        lc_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                # `tool_calls` (if this assistant turn made any) must be
                # attached to the `AIMessage` itself, not inferred from the
                # following `tool` messages -- LangChain's OpenAI serializer
                # only emits a wire-format `tool_calls` array when
                # `AIMessage.tool_calls` is set, and a `tool`-role message
                # with no preceding `tool_calls` array is a 400 from the API
                # ("messages with role 'tool' must be a response to a
                # preceding message with 'tool_calls'"), not a silent no-op.
                raw_tool_calls = m.get("tool_calls") or []
                lc_tool_calls = [
                    {"id": tc["id"], "name": tc["name"], "args": tc["arguments"]} for tc in raw_tool_calls
                ]
                lc_messages.append(AIMessage(content=content, tool_calls=lc_tool_calls))
            elif role == "tool":
                lc_messages.append(ToolMessage(content=content, tool_call_id=m["tool_call_id"]))
            else:
                raise ValueError(f"Unsupported message role: {role!r}")
        return lc_messages

    def call_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatTurnResult:
        bound = self._llm.bind_tools(tools) if tools else self._llm
        lc_messages = self._to_lc_messages(system_prompt, messages)
        result = self._invoke_with_retry(bound, lc_messages)
        raw_tool_calls = getattr(result, "tool_calls", None) or []
        tool_calls = [ToolCall(id=tc["id"], name=tc["name"], arguments=tc["args"]) for tc in raw_tool_calls]
        content = result.content if isinstance(result.content, str) else None
        return ChatTurnResult(tool_calls=tool_calls, content=content)

    def call_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        output_schema: type[BaseModel],
    ) -> BaseModel:
        structured_llm = self._llm.with_structured_output(output_schema)
        lc_messages = self._to_lc_messages(system_prompt, messages)
        result = self._invoke_with_retry(structured_llm, lc_messages)
        if isinstance(result, output_schema):
            return result
        if isinstance(result, dict):
            return output_schema(**result)
        raise TypeError(
            f"Azure OpenAI structured output for {output_schema.__name__} was neither an instance "
            f"of that schema nor a dict: {type(result)!r}"
        )
