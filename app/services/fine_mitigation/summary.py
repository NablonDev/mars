"""Service for generating and persisting fine mitigation summaries.

Full mirror of app/services/fine_projection/summary.py's shape:
get_or_schedule / run_generation / get_status, fingerprint-based reuse, and
the same bounded tool-calling loop. The one deliberate difference: the
fingerprint hashes the mitigation options themselves (what actually
determines whether the narrative would change), not projection-specific
fields like days_to_delivery -- see _compute_content_fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.fine_mitigation import (
    ActualOutcome,
    FineMitigationSummaryContext,
    FineMitigationSummaryOutput,
    MitigationOptionContext,
    OrderContext,
    build_fine_mitigation_summary_tools,
)
from app.agents.fine_mitigation.prompts.v1 import PROMPT_VERSION, SYSTEM_PROMPT
from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import get_settings
from app.core.exceptions import (
    InvalidAsOfDateError,
    NoMitigationOptionsExistError,
    NoSummaryJobExistsError,
    OrderNotFoundError,
    ToolLoopExhaustedError,
)
from app.models.enums import SummaryStatus
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_master_data import MasterDataRepository
from app.repositories.fine_mitigation.mitigation import MitigationResultRepository
from app.repositories.fine_mitigation.summary import FineMitigationSummaryRepository
from app.repositories.order import OrderRepository

MAX_TOOL_ROUNDS = 4
_UPSTREAM_FAILURE_MESSAGE = "Fine mitigation summary generation failed upstream"

# Registered lazily on the service's read/write path rather than at startup.
_AGENT_NAME = "fine_mitigation_summary"
_AGENT_SOURCE = "fine_mitigation"
_PROMPT_MODULE_PATH = "app.agents.fine_mitigation.prompts.v1"
_LLM_PROVIDER = "azure_openai"

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _wrap_data(payload: dict | list) -> str:
    body = json.dumps(payload, default=_json_default, sort_keys=True)
    return f"<DATA>\n{body}\n</DATA>"


def _fmt_number(value: float) -> str:
    """Format numbers deterministically for fingerprinting."""
    return f"{float(value):.6f}"


def _compute_content_fingerprint(mandatory_context: FineMitigationSummaryContext) -> str:
    """Hash the facts that determine the generated narrative.

    Unlike fine_projection_summary's fingerprint (which hashes the current
    day's violations/statuses), this hashes the ranked mitigation options
    themselves -- the actual ground truth the narrative is built from --
    plus the baseline fine and order status. Excludes current_projection_date:
    a narrative whose options are byte-for-byte identical to yesterday's
    should be reusable even though the date changed.
    """
    options = sorted(
        (
            o.action,
            _fmt_number(o.projected_fine_after),
            _fmt_number(o.action_cost),
            _fmt_number(o.net_saving),
            o.risk_level,
            o.confidence,
        )
        for o in mandatory_context.mitigation_options
    )

    payload = {
        "options": options,
        "current_total_expected_fine": _fmt_number(mandatory_context.current_total_expected_fine),
        "stacking_mode": mandatory_context.stacking_mode,
        "order_status": mandatory_context.order.order_status,
        "has_actual_outcomes": mandatory_context.actual_outcomes is not None,
    }

    body = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class FineMitigationSummaryOutputWithReuse(FineMitigationSummaryOutput):
    """FineMitigationSummaryOutput with fields describing narrative reuse."""

    is_reused: bool = False
    generated_for_date: date | None = None
    unchanged_since: date | None = None
    unchanged_for_days: int | None = None


@dataclass
class FineMitigationSummaryJob:
    order_id: str
    as_of_date: date
    prompt_version: str
    status: SummaryStatus
    output: FineMitigationSummaryOutput | None = None
    error_message: str | None = None


@dataclass
class FineMitigationSummaryService:
    orders: OrderRepository
    master_data: MasterDataRepository
    mitigation_results: MitigationResultRepository
    summaries: FineMitigationSummaryRepository
    prompt_registry: PromptRegistryRepository
    llm: AzureOpenAIChatClient

    def _ensure_registered(self) -> UUID:
        return self.prompt_registry.ensure_registered(
            agent_name=_AGENT_NAME,
            prompt_version=PROMPT_VERSION,
            module_path=_PROMPT_MODULE_PATH,
            provider=_LLM_PROVIDER,
            source=_AGENT_SOURCE,
        )

    def get_or_schedule(
        self,
        order_id: str,
        as_of_date: date | None = None,
        force_regenerate: bool = False,
    ) -> FineMitigationSummaryJob:
        order, as_of_date, options_rows = self._validate(order_id, as_of_date)

        mandatory_context = self._assemble_mandatory_context(order, as_of_date, options_rows)
        context_hash = hashlib.sha256(
            json.dumps(
                mandatory_context.model_dump(mode="json"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        content_fingerprint = _compute_content_fingerprint(mandatory_context)
        logger.info(
            "Fine mitigation summary content fingerprint: order_id=%s as_of_date=%s fingerprint=%s",
            order_id,
            as_of_date,
            content_fingerprint,
        )

        if not force_regenerate:
            cached = self.summaries.get_cached(order_id, as_of_date, PROMPT_VERSION)
            if cached is not None:
                return FineMitigationSummaryJob(
                    order_id=order_id,
                    as_of_date=as_of_date,
                    prompt_version=PROMPT_VERSION,
                    status=SummaryStatus.READY,
                    output=self._to_output(cached),
                )

            if get_settings().summary_reuse_enabled:
                reused = self._try_reuse(order_id, as_of_date, content_fingerprint, context_hash)
                if reused is not None:
                    return FineMitigationSummaryJob(
                        order_id=order_id,
                        as_of_date=as_of_date,
                        prompt_version=PROMPT_VERSION,
                        status=SummaryStatus.READY,
                        output=self._to_output(reused),
                    )

        agent_id = self._ensure_registered()
        self.summaries.create_pending(
            order_id=order_id,
            as_of_date=as_of_date,
            agent_id=agent_id,
            prompt_version=PROMPT_VERSION,
            context_hash=context_hash,
            content_fingerprint=content_fingerprint,
        )
        self.summaries.commit()

        return FineMitigationSummaryJob(
            order_id=order_id,
            as_of_date=as_of_date,
            prompt_version=PROMPT_VERSION,
            status=SummaryStatus.PENDING,
        )

    def _try_reuse(
        self,
        order_id: str,
        as_of_date: date,
        content_fingerprint: str,
        context_hash: str,
    ) -> dict | None:
        """Reuse the latest matching narrative within the configured window."""
        settings = get_settings()
        earliest_source_date = as_of_date - timedelta(days=settings.summary_max_reuse_days)

        candidate = self.summaries.find_reusable(
            order_id=order_id,
            prompt_version=PROMPT_VERSION,
            content_fingerprint=content_fingerprint,
            earliest_source_date=earliest_source_date,
            not_after=as_of_date,
        )
        if candidate is None:
            return None

        original_source_date = candidate["source_as_of_date"] or candidate["as_of_date"]

        reused = self.summaries.create_reused(
            order_id=order_id,
            as_of_date=as_of_date,
            agent_id=self._ensure_registered(),
            prompt_version=PROMPT_VERSION,
            context_hash=context_hash,
            content_fingerprint=content_fingerprint,
            model_name=candidate["model_name"],
            summary=candidate["summary"],
            source_as_of_date=original_source_date,
        )
        self.summaries.commit()

        logger.info(
            "Fine mitigation summary reused: order_id=%s as_of_date=%s source_as_of_date=%s fingerprint=%s",
            order_id,
            as_of_date,
            original_source_date,
            content_fingerprint,
        )

        return reused

    def get_status(
        self,
        order_id: str,
        as_of_date: date | None = None,
    ) -> FineMitigationSummaryJob:
        _, as_of_date, _ = self._validate(order_id, as_of_date)

        row = self.summaries.get_by_key(order_id, as_of_date, PROMPT_VERSION)
        if row is None:
            row = self.summaries.get_latest_ready_not_after(order_id, as_of_date, PROMPT_VERSION)
        if row is None:
            raise NoSummaryJobExistsError(order_id, as_of_date, domain="mitigation")

        output = self._to_output(row) if row["summary"] is not None else None

        return FineMitigationSummaryJob(
            order_id=order_id,
            as_of_date=row["as_of_date"],
            prompt_version=PROMPT_VERSION,
            status=row["status"],
            output=output,
            error_message=row["error_message"],
        )

    @staticmethod
    def _to_output(row: dict) -> FineMitigationSummaryOutput:
        source_as_of_date = row.get("source_as_of_date")
        is_reused = source_as_of_date is not None
        as_of_date = row["as_of_date"]

        return FineMitigationSummaryOutputWithReuse(
            order_id=row["order_id"],
            as_of_date=as_of_date,
            prompt_version=row["prompt_version"],
            model_name=row["model_name"],
            summary=row["summary"],
            is_reused=is_reused,
            generated_for_date=source_as_of_date if is_reused else as_of_date,
            unchanged_since=source_as_of_date if is_reused else None,
            unchanged_for_days=(as_of_date - source_as_of_date).days if is_reused else None,
        )

    def run_generation(
        self,
        order_id: str,
        as_of_date: date,
        prompt_version: str,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        """Generate the summary and persist success or failure.

        Failures are persisted and re-raised so queue callers can apply their
        retry/dead-letter policy. Heartbeats are best-effort and never abort
        generation.
        """
        order = self.orders.get_order(order_id)
        if order is None:
            logger.error(
                "Fine mitigation summary job: order %s no longer exists",
                order_id,
            )
            return

        try:
            options_rows = self.mitigation_results.get_latest_not_after(order_id, as_of_date)
            mandatory_context = self._assemble_mandatory_context(order, as_of_date, options_rows)
            content_fingerprint = _compute_content_fingerprint(mandatory_context)
            logger.info(
                "Fine mitigation summary content fingerprint: order_id=%s as_of_date=%s fingerprint=%s",
                order_id,
                as_of_date,
                content_fingerprint,
            )
            summary = self._run_tool_loop(
                order_id,
                order["order_status"],
                as_of_date,
                mandatory_context,
                heartbeat=heartbeat,
            )
        except ToolLoopExhaustedError as exc:
            logger.error(
                "Fine mitigation summary generation failed: order_id=%s date=%s: %s",
                order_id,
                as_of_date,
                exc.detail or exc.message,
            )
            self._safe_mark_failed(order_id, as_of_date, prompt_version, exc.message)
            raise
        except Exception:
            logger.exception(
                "Fine mitigation summary generation crashed: order_id=%s date=%s",
                order_id,
                as_of_date,
            )
            self._safe_mark_failed(order_id, as_of_date, prompt_version, _UPSTREAM_FAILURE_MESSAGE)
            raise

        self.summaries.mark_ready(
            order_id=order_id,
            as_of_date=as_of_date,
            agent_id=self._ensure_registered(),
            prompt_version=prompt_version,
            model_name=self.llm.model_name,
            summary=summary,
            content_fingerprint=content_fingerprint,
        )

    def _safe_mark_failed(
        self,
        order_id: str,
        as_of_date: date,
        prompt_version: str,
        error_message: str,
    ) -> None:
        try:
            self.summaries.mark_failed(
                order_id,
                as_of_date,
                self._ensure_registered(),
                prompt_version,
                error_message,
            )
        except Exception:
            logger.exception(
                "Failed to persist fine mitigation summary failure: order_id=%s date=%s",
                order_id,
                as_of_date,
            )

    def _validate(
        self,
        order_id: str,
        as_of_date: date | None,
    ) -> tuple[dict, date, list[dict]]:
        logger.info(
            "Fine mitigation summary requested for order_id=%s as_of_date=%s",
            order_id,
            as_of_date,
        )

        order = self.orders.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        today = datetime.now(UTC).date()
        as_of_date = as_of_date or today

        earliest_options_date = self.mitigation_results.earliest_date(order_id)
        if earliest_options_date is None:
            raise NoMitigationOptionsExistError(f"No mitigation options exist yet for order_id={order_id!r}.")

        if as_of_date > today:
            raise InvalidAsOfDateError(f"as_of_date={as_of_date!r} is in the future.")

        if as_of_date < earliest_options_date:
            raise InvalidAsOfDateError(
                f"as_of_date={as_of_date!r} predates the earliest "
                f"mitigation-options date, {earliest_options_date!r}."
            )

        options_rows = self.mitigation_results.get_latest_not_after(order_id, as_of_date)
        return order, as_of_date, options_rows

    def _assemble_mandatory_context(
        self,
        order: dict,
        as_of_date: date,
        options_rows: list[dict],
    ) -> FineMitigationSummaryContext:
        order_id = order["order_id"]
        retailer_id = order["retailer_id"]

        retailer_name = next(
            (
                r["retailer_name"]
                for r in self.master_data.list_retailers()
                if r["retailer_id"] == retailer_id
            ),
            retailer_id,
        )
        sku = next((s for s in self.master_data.list_skus() if s["sku_id"] == order["sku_id"]), None)
        sku_description = (sku["description"] or sku["sku_code"]) if sku else order["sku_id"]
        carrier_name = None
        if order["carrier_id"]:
            carrier_name = next(
                (
                    c["carrier_name"]
                    for c in self.master_data.list_carriers()
                    if c["carrier_id"] == order["carrier_id"]
                ),
                None,
            )

        stacking_mode = self.master_data.get_stacking_mode(retailer_id)

        mitigation_options = [
            MitigationOptionContext(
                action=row["action"],
                projected_fine_after=row["projected_fine_after"],
                action_cost=row["action_cost"],
                net_saving=row["net_saving"],
                risk_level=row["risk_level"],
                confidence=row["confidence"],
                rationale=row["rationale"],
            )
            for row in options_rows
        ]

        accept_row = next((row for row in options_rows if row["action"] == "ACCEPT"), None)
        current_total_expected_fine = accept_row["projected_fine_after"] if accept_row else 0.0

        actual_outcomes = None
        if order["order_status"] == "DELIVERED":
            actual_outcomes = [
                ActualOutcome(
                    violation_type=fine["violation_type"],
                    actual_fine_amount=fine["actual_fine_amount"],
                    invoice_or_deduction_date=fine["invoice_or_deduction_date"],
                )
                for fine in self.orders.list_actual_fines(order_id)
            ]

        return FineMitigationSummaryContext(
            order=OrderContext(
                order_id=order_id,
                order_status=order["order_status"],
                retailer_name=retailer_name,
                sku_description=sku_description,
                order_qty=order["order_qty"],
                unit_price=order["unit_price"],
                required_ship_date=order["required_ship_date"],
                requested_delivery_date=order["requested_delivery_date"],
                carrier_id=order["carrier_id"],
                carrier_name=carrier_name,
            ),
            current_projection_date=as_of_date,
            current_total_expected_fine=current_total_expected_fine,
            stacking_mode=stacking_mode,
            mitigation_options=mitigation_options,
            actual_outcomes=actual_outcomes,
        )

    def _run_tool_loop(
        self,
        order_id: str,
        order_status: str,
        as_of_date: date,
        mandatory_context: FineMitigationSummaryContext,
        heartbeat: Callable[[], None] | None = None,
    ) -> str:
        messages: list[BaseMessage] = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=_wrap_data(mandatory_context.model_dump(mode="json"))),
        ]
        tools = build_fine_mitigation_summary_tools(
            carrier_reliability=lambda carrier_id: self._get_carrier_reliability(carrier_id),
            actual_fines=lambda: self.orders.list_actual_fines(order_id),
            order_status=order_status,
        )

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
                response = self.llm.invoke(messages, tools=tools)

                logger.info(
                    "Fine mitigation summary LLM round %s completed in %.1fs",
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

            final_response = self.llm.invoke(messages)

        except Exception as exc:
            raise ToolLoopExhaustedError(
                _UPSTREAM_FAILURE_MESSAGE,
                domain="mitigation",
                detail=(
                    f"Fine mitigation summary generation failed after "
                    f"{MAX_TOOL_ROUNDS} rounds limit for order_id={order_id!r}, "
                    f"as_of_date={as_of_date!r}: {exc}"
                ),
            ) from exc

        if not final_response.content:
            raise ToolLoopExhaustedError(
                _UPSTREAM_FAILURE_MESSAGE,
                domain="mitigation",
                detail=(
                    f"Fine mitigation summary generation failed: "
                    f"Model returned no summary "
                    f"for order_id={order_id!r}, as_of_date={as_of_date!r}."
                ),
            )

        if not isinstance(final_response.content, str):
            raise ToolLoopExhaustedError(
                _UPSTREAM_FAILURE_MESSAGE,
                domain="mitigation",
                detail=(
                    f"Fine mitigation summary generation failed: "
                    f"Model returned non-text final content "
                    f"for order_id={order_id!r}, as_of_date={as_of_date!r}."
                ),
            )

        return final_response.content

    @staticmethod
    def _invoke_heartbeat(heartbeat: Callable[[], None] | None, order_id: str) -> None:
        """Best-effort heartbeat; callback failures never abort generation."""
        if heartbeat is None:
            return
        try:
            heartbeat()
        except Exception:
            logger.exception(
                "Fine mitigation summary heartbeat callback failed for order_id=%s; continuing generation",
                order_id,
            )

    def _get_carrier_reliability(
        self,
        carrier_id: str,
    ) -> dict[str, Any]:
        carriers = self.master_data.list_carriers()
        carrier = next(
            (carrier for carrier in carriers if carrier["carrier_id"] == carrier_id),
            None,
        )
        return carrier or {
            "carrier_id": carrier_id,
            "found": False,
        }
