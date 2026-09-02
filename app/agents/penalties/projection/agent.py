"""`PenaltyProjectionAgent`: owns the bounded tool-calling loop for
penalty-projection-summary generation.

Extracted from `app.services.penalties._summary_base.SummaryServiceBase`'s
former `_run_tool_loop` (itself inline in `FineProjectionSummaryService`
before that). `ProjectionSummaryService` now only assembles context/tools
and delegates generation to this class via `generate_projection_summary`.

The system prompt is loaded at call time from `process.agent`'s currently
`is_active` row for `agent_code` (via `AgentRegistryRepository.get_active`)
-- never imported directly from `app.agents.penalties.projection.prompts.v*`.
That keeps prompt content swappable by a DB update, without a code deploy.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.agents.penalties.projection.context import PenaltyProjectionSummaryContext
from app.agents.penalties.projection.schema import PenaltyProjectionSummaryOutput
from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.exceptions import ExternalServiceError
from app.repositories.process.agent_registry import AgentRegistryRepository

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4

#: `ExternalServiceError(code=...)` for an exhausted/failed tool-calling loop
#: -- keyed by `summary_domain` ("projection" | "mitigation").
_UPSTREAM_FAILURE_CODES: dict[str, str] = {
    "projection": "PENALTY_PROJECTION_SUMMARY_UPSTREAM_FAILED",
    "mitigation": "PENALTY_MITIGATION_SUMMARY_UPSTREAM_FAILED",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _wrap_data(payload: dict | list) -> str:
    body = json.dumps(payload, default=_json_default, sort_keys=True)
    return f"<DATA>\n{body}\n</DATA>"


class PenaltyProjectionAgent:
    """The projection-summary sub-domain's LLM tool-calling loop."""

    def __init__(
        self,
        *,
        llm: AzureOpenAIChatClient,
        agent_registry: AgentRegistryRepository,
        agent_code: str,
        summary_domain: str,
        upstream_failure_message: str,
    ) -> None:
        self._llm = llm
        self._agent_registry = agent_registry
        self._agent_code = agent_code
        self._summary_domain = summary_domain
        self._upstream_failure_message = upstream_failure_message

    def generate_projection_summary(
        self,
        context: PenaltyProjectionSummaryContext,
        *,
        order_id: str,
        as_of_date: date,
        tools: list[BaseTool],
        heartbeat: Callable[[], None] | None = None,
    ) -> PenaltyProjectionSummaryOutput:
        active_agent = self._active_agent_row()
        messages: list[BaseMessage] = [
            SystemMessage(content=active_agent["system_prompt"]),
            HumanMessage(content=_wrap_data(context.model_dump(mode="json"))),
        ]

        try:
            for round_number in range(1, MAX_TOOL_ROUNDS):
                # Heartbeat between LLM calls keeps long-running workers alive.
                self._invoke_heartbeat(heartbeat, order_id)

                logger.info(
                    "Calling LLM for order_id=%s round=%s/%s",
                    order_id,
                    round_number,
                    MAX_TOOL_ROUNDS - 1,
                )

                started = time.monotonic()
                response = self._llm.invoke(messages, tools=tools)

                logger.info(
                    "Projection summary LLM round %s completed in %.1fs",
                    round_number,
                    time.monotonic() - started,
                )

                if not response.tool_calls:
                    break

                messages.append(response)
                tool_map = {tool.name: tool for tool in tools}

                for tool_call in response.tool_calls:
                    tool = tool_map.get(tool_call["name"])
                    if tool is None:
                        raise ValueError(f"Unknown tool returned by model: {tool_call['name']!r}")

                    result = tool.invoke(tool_call["args"])
                    messages.append(
                        ToolMessage(
                            content=_wrap_data(result),
                            tool_call_id=tool_call["id"],
                        )
                    )

            final_response = self._llm.invoke(messages)

        except Exception as exc:
            raise ExternalServiceError(
                code=_UPSTREAM_FAILURE_CODES[self._summary_domain],
                message=self._upstream_failure_message,
                details={
                    "detail": (
                        f"projection summary generation failed after {MAX_TOOL_ROUNDS} rounds "
                        f"limit for order_id={order_id!r}, as_of_date={as_of_date.isoformat()}: {exc}"
                    )
                },
            ) from exc

        if not final_response.content:
            raise ExternalServiceError(
                code=_UPSTREAM_FAILURE_CODES[self._summary_domain],
                message=self._upstream_failure_message,
                details={
                    "detail": (
                        f"projection summary generation failed: Model returned no summary "
                        f"for order_id={order_id!r}, as_of_date={as_of_date.isoformat()}."
                    )
                },
            )

        if not isinstance(final_response.content, str):
            raise ExternalServiceError(
                code=_UPSTREAM_FAILURE_CODES[self._summary_domain],
                message=self._upstream_failure_message,
                details={
                    "detail": (
                        f"projection summary generation failed: Model returned non-text final "
                        f"content for order_id={order_id!r}, as_of_date={as_of_date.isoformat()}."
                    )
                },
            )

        return PenaltyProjectionSummaryOutput(
            order_id=order_id,
            as_of_date=as_of_date,
            prompt_version=active_agent["prompt_version"],
            model_name=self._llm.model_name,
            summary=final_response.content,
        )

    def _active_agent_row(self) -> dict:
        active = self._agent_registry.get_active(self._agent_code)
        if active is None:
            raise ExternalServiceError(
                code=_UPSTREAM_FAILURE_CODES[self._summary_domain],
                message=self._upstream_failure_message,
                details={
                    "detail": f"No active process.agent row registered for agent_code={self._agent_code!r}."
                },
            )
        return active

    @staticmethod
    def _invoke_heartbeat(heartbeat: Callable[[], None] | None, order_id: str) -> None:
        """Best-effort heartbeat; callback failures never abort generation."""
        if heartbeat is None:
            return
        try:
            heartbeat()
        except Exception:
            logger.exception(
                "Summary heartbeat callback failed for order_id=%s; continuing generation",
                order_id,
            )
