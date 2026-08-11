"""
Provider-agnostic chat-client contract for `ExplanationService`. Deliberately
plain dataclasses/dicts, not LangChain message types -- so a hand-written
fake used in unit tests (see tests/services/test_explanation_service.py)
never needs to import or mimic LangChain's wire format, and so a future
non-LangChain provider could implement this Protocol without depending on
LangChain at all.

`messages` passed to both methods are `{"role": ..., "content": ...}`
dicts (roles: "user", "assistant", "tool"; "tool" messages also carry
`tool_call_id`). An "assistant" message that made tool calls must also
carry `"tool_calls": [{"id", "name", "arguments"}, ...]` (the same shape
as `ToolCall` below) -- a provider needs that to reconstruct a real
assistant turn that the following "tool" messages can attach to; dropping
it and keeping only `content` produces a message history that looks fine
in Python but a real provider will reject (a "tool" message with no
preceding message carrying its `tool_calls` id is invalid, not silently
ignored). Every piece of retrieved/tool-sourced content must already be
wrapped in a delimited `<DATA>` block by the caller before it reaches a
message here -- see `.claude/skills/llm-agent-patterns/SKILL.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatTurnResult:
    """Result of one round of the tool-calling loop."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    content: str | None = None


class StructuredChatClient(Protocol):
    """What `ExplanationService` needs from an LLM provider. Two distinct
    calls, not one that composes tool-calling with structured output --
    see providers/azure_openai.py for why that composition isn't relied
    on with the pinned langchain-openai version."""

    def call_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatTurnResult:
        """One round where the model may choose to call zero or more of
        `tools`. Returns the tool calls it made (if any) and/or plain
        text content."""
        ...

    def call_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        output_schema: type[BaseModel],
    ) -> BaseModel:
        """Tools disabled -- forces the model to return output shaped
        exactly like `output_schema`. Raises if the provider can't
        produce valid structured output."""
        ...
