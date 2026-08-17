"""Service for generating and persisting fine summaries."""

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

from app.agents.fine_summary_context import (
    ActiveRule,
    ActualOutcome,
    DailyHistoryEntry,
    FineSummaryContext,
    OrderContext,
    TierBand,
    ViolationEntry,
)
from app.agents.fine_summary_schema import FineSummaryOutput
from app.agents.prompts.fine_summary.v3 import PROMPT_VERSION, SYSTEM_PROMPT
from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.agents.tools.fine_summary import build_fine_summary_tools
from app.core.config import get_settings
from app.core.exceptions import (
    InvalidAsOfDateError,
    NoProjectionExistsError,
    NoSummaryJobExistsError,
    OrderNotFoundError,
    ToolLoopExhaustedError,
)
from app.models.enums import SummaryStatus
from app.repositories.agent_registry import PromptRegistryRepository
from app.repositories.fine_rule import FineRuleRepository
from app.repositories.fine_summary import FineSummaryRepository
from app.repositories.master_data import MasterDataRepository
from app.repositories.order import OrderRepository
from app.repositories.projection import ProjectionRepository
from app.services.fine_projection import DELAY_VIOLATION_TYPES, SHORTAGE_VIOLATION_TYPES

MAX_TOOL_ROUNDS = 4
_UPSTREAM_FAILURE_MESSAGE = "Fine summary generation failed upstream"

# Registered lazily on the service's read/write path rather than at startup.
_AGENT_NAME = "fine_summary"
_AGENT_SOURCE = "fines"
_PROMPT_MODULE_PATH = "app.agents.prompts.fine_summary.v3"
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


def _compute_content_fingerprint(mandatory_context: FineSummaryContext) -> str:
    """Hash the facts that determine the generated narrative.

    Excludes dates and other values that may change without changing the
    narrative, such as `days_to_delivery`.
    """
    current_entry = mandatory_context.daily_history[-1] if mandatory_context.daily_history else None

    violations = sorted(
        (v.violation_type, _fmt_number(v.probability), _fmt_number(v.expected_fine))
        for v in (current_entry.violations if current_entry is not None else [])
    )
    active_rule_ids = sorted(rule.rule_id for rule in mandatory_context.active_rules)

    payload = {
        "violations": violations,
        "stacking_mode": mandatory_context.stacking_mode,
        "total_expected_fine": _fmt_number(current_entry.total_expected_fine if current_entry else 0.0),
        "order_status": mandatory_context.order.order_status,
        "confirmed_qty": current_entry.confirmed_qty if current_entry else None,
        "production_status": current_entry.production_status if current_entry else None,
        "appointment_status": current_entry.appointment_status if current_entry else None,
        "actual_ship_date": (
            current_entry.actual_ship_date.isoformat()
            if current_entry is not None and current_entry.actual_ship_date is not None
            else None
        ),
        "expected_ship_date_override": (
            current_entry.expected_ship_date_override.isoformat()
            if current_entry is not None and current_entry.expected_ship_date_override is not None
            else None
        ),
        "demand_exception_flagged": current_entry.demand_exception_flagged if current_entry else None,
        "active_rule_ids": active_rule_ids,
    }

    body = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class FineSummaryOutputWithReuse(FineSummaryOutput):
    """FineSummaryOutput with fields describing narrative reuse."""

    is_reused: bool = False
    generated_for_date: date | None = None
    unchanged_since: date | None = None
    unchanged_for_days: int | None = None


@dataclass
class FineSummaryJob:
    order_id: str
    as_of_date: date
    prompt_version: str
    status: SummaryStatus
    output: FineSummaryOutput | None = None
    error_message: str | None = None


@dataclass
class FineSummaryService:
    orders: OrderRepository
    rules: FineRuleRepository
    master_data: MasterDataRepository
    projections: ProjectionRepository
    summaries: FineSummaryRepository
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
    ) -> FineSummaryJob:
        order, as_of_date, history = self._validate(order_id, as_of_date)

        mandatory_context = self._assemble_mandatory_context(
            order,
            as_of_date,
            history,
        )
        context_hash = hashlib.sha256(
            json.dumps(
                mandatory_context.model_dump(mode="json"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        content_fingerprint = _compute_content_fingerprint(mandatory_context)
        logger.info(
            "Fine summary content fingerprint: order_id=%s as_of_date=%s fingerprint=%s",
            order_id,
            as_of_date,
            content_fingerprint,
        )

        if not force_regenerate:
            cached = self.summaries.get_cached(
                order_id,
                as_of_date,
                PROMPT_VERSION,
            )
            if cached is not None:
                return FineSummaryJob(
                    order_id=order_id,
                    as_of_date=as_of_date,
                    prompt_version=PROMPT_VERSION,
                    status=SummaryStatus.READY,
                    output=self._to_output(cached),
                )

            if get_settings().summary_reuse_enabled:
                reused = self._try_reuse(order_id, as_of_date, content_fingerprint, context_hash)
                if reused is not None:
                    return FineSummaryJob(
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

        return FineSummaryJob(
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
            "Fine summary reused: order_id=%s as_of_date=%s source_as_of_date=%s fingerprint=%s",
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
    ) -> FineSummaryJob:
        _, as_of_date, _ = self._validate(order_id, as_of_date)

        row = self.summaries.get_by_key(
            order_id,
            as_of_date,
            PROMPT_VERSION,
        )
        if row is None:
            raise NoSummaryJobExistsError(order_id, as_of_date)

        output = self._to_output(row) if row["summary"] is not None else None

        return FineSummaryJob(
            order_id=order_id,
            as_of_date=as_of_date,
            prompt_version=PROMPT_VERSION,
            status=row["status"],
            output=output,
            error_message=row["error_message"],
        )

    @staticmethod
    def _to_output(row: dict) -> FineSummaryOutput:
        source_as_of_date = row.get("source_as_of_date")
        is_reused = source_as_of_date is not None
        as_of_date = row["as_of_date"]

        return FineSummaryOutputWithReuse(
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
    ) ->  None:
        """Generate the summary and persist success or failure.

        Failures are persisted and re-raised so queue callers can apply their
        retry/dead-letter policy. Heartbeats are best-effort and never abort
        generation.
        """
        order = self.orders.get_order(order_id)
        if order is None:
            logger.error(
                "Fine summary job: order %s no longer exists",
                order_id,
            )
            return

        try:
            history = self.projections.get_history(order_id)
            mandatory_context = self._assemble_mandatory_context(
                order,
                as_of_date,
                history,
            )
            content_fingerprint = _compute_content_fingerprint(mandatory_context)
            logger.info(
                "Fine summary content fingerprint: order_id=%s as_of_date=%s fingerprint=%s",
                order_id,
                as_of_date,
                content_fingerprint,
            )
            summary = self._run_tool_loop(
                order_id,
                order["retailer_id"],
                order["order_status"],
                as_of_date,
                mandatory_context,
                heartbeat=heartbeat,
            )
        except ToolLoopExhaustedError as exc:
            logger.error(
                "Fine summary generation failed: order_id=%s date=%s: %s",
                order_id,
                as_of_date,
                exc.detail or exc.message,
            )
            self._safe_mark_failed(
                order_id,
                as_of_date,
                prompt_version,
                exc.message,
            )
            raise
        except Exception:
            logger.exception(
                "Fine summary generation crashed: order_id=%s date=%s",
                order_id,
                as_of_date,
            )
            self._safe_mark_failed(
                order_id,
                as_of_date,
                prompt_version,
                _UPSTREAM_FAILURE_MESSAGE,
            )
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
                "Failed to persist fine summary failure: order_id=%s date=%s",
                order_id,
                as_of_date,
            )

    def _validate(
        self,
        order_id: str,
        as_of_date: date | None,
    ) -> tuple[dict, date, list[dict]]:
        logger.info(
            "Fine summary requested for order_id=%s as_of_date=%s",
            order_id,
            as_of_date,
        )

        order = self.orders.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)

        today = datetime.now(UTC).date()
        as_of_date = as_of_date or today

        history = self.projections.get_history(order_id)
        if not history:
            raise NoProjectionExistsError(f"No projections exist yet for order_id={order_id!r}.")

        earliest_projection_date = min(row["projection_date"] for row in history)

        if as_of_date > today:
            raise InvalidAsOfDateError(f"as_of_date={as_of_date!r} is in the future.")

        if as_of_date < earliest_projection_date:
            raise InvalidAsOfDateError(
                f"as_of_date={as_of_date!r} predates the earliest "
                f"projection date, {earliest_projection_date!r}."
            )

        return order, as_of_date, history

    def _assemble_mandatory_context(
        self,
        order: dict,
        as_of_date: date,
        history: list[dict],
    ) -> FineSummaryContext:
        order_id = order["order_id"]
        retailer_id = order["retailer_id"]
        rules = self.rules.get_rules_for_retailer(retailer_id)

        # Bound every history row to <= as_of_date -- otherwise a
        # historical/backfilled request leaks future rows into the
        # context and "current" becomes ambiguous.
        bounded_history = [row for row in history if row["projection_date"] <= as_of_date]

        active_rules = [
            ActiveRule(
                rule_id=rule.rule_id,
                violation_type=rule.violation_type,
                calc_type=rule.calc_type.value,
                rate=rule.rate,
                threshold_pct=rule.threshold_pct,
                cap_amount=rule.cap_amount,
                tiers=[
                    TierBand(band_min=tier.band_min, band_max=tier.band_max, rate=tier.rate)
                    for tier in (rule.tiers or [])
                ]
                or None,
            )
            for rule in rules
        ]

        daily_history = self._build_daily_history(order_id, bounded_history)

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

        other_open_orders = self.orders.list_open_orders_for_sku_location(
            order["sku_id"],
            order["ship_from_location_id"],
            order_id,
        )

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

        return FineSummaryContext(
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
            stacking_mode=self.master_data.get_stacking_mode(retailer_id),
            active_rules=active_rules,
            daily_history=daily_history,
            shared_production_line=bool(other_open_orders),
            other_open_orders_same_sku_location=other_open_orders,
            actual_outcomes=actual_outcomes,
        )

    def _build_daily_history(
        self,
        order_id: str,
        bounded_history: list[dict],
    ) -> list[DailyHistoryEntry]:
        """One entry per distinct projection_date, combining that day's
        engine outputs (from fact_projected_fine, via bounded_history) with
        that day's inputs (via OrderRepository.build_snapshot)"""
        entries: list[DailyHistoryEntry] = []

        for day in sorted({row["projection_date"] for row in bounded_history}):
            day_rows = [row for row in bounded_history if row["projection_date"] == day]
            snapshot = self.orders.build_snapshot(order_id, day)

            shortage_probability = next(
                (
                    r["failure_probability"]
                    for r in day_rows
                    if r["violation_type"] in SHORTAGE_VIOLATION_TYPES
                ),
                0.0,
            )
            delay_probability = next(
                (r["failure_probability"] for r in day_rows if r["violation_type"] in DELAY_VIOLATION_TYPES),
                0.0,
            )

            violations = [
                ViolationEntry(
                    violation_type=r["violation_type"],
                    rule_id=r["rule_id"],
                    probability=r["failure_probability"],
                    fine_if_realized=(
                        round(r["projected_fine_amount"] / r["failure_probability"], 2)
                        if r["failure_probability"]
                        else None
                    ),
                    expected_fine=r["projected_fine_amount"],
                )
                for r in day_rows
            ]

            entries.append(
                DailyHistoryEntry(
                    entry_date=day,
                    confirmed_qty=snapshot.confirmed_qty,
                    production_status=snapshot.production_status.value,
                    appointment_status=snapshot.appointment_status.value,
                    actual_ship_date=snapshot.actual_ship_date,
                    expected_ship_date_override=snapshot.expected_ship_date,
                    demand_exception_flagged=snapshot.demand_exception_flagged,
                    days_to_delivery=day_rows[0]["days_to_delivery"],
                    shortage_probability=shortage_probability,
                    delay_probability=delay_probability,
                    violations=violations,
                    total_expected_fine=round(sum(r["projected_fine_amount"] for r in day_rows), 2),
                )
            )
        return entries

    def _run_tool_loop(
        self,
        order_id: str,
        retailer_id: str,
        order_status: str,
        as_of_date: date,
        mandatory_context: FineSummaryContext,
        heartbeat: Callable[[], None] | None = None,
    ) -> str:
        messages: list[BaseMessage] = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=_wrap_data(mandatory_context.model_dump(mode="json"))),
        ]
        tools = build_fine_summary_tools(
            carrier_reliability=lambda carrier_id: self._get_carrier_reliability(carrier_id),
            actual_fines=lambda: self.orders.list_actual_fines(order_id),
            tier_bands=lambda rule_id: self._get_tier_bands(retailer_id, rule_id),
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
                    "Fine summary LLM round %s completed in %.1fs",
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
                detail=(
                    f"Fine summary generation failed after "
                    f"{MAX_TOOL_ROUNDS} rounds limit for order_id={order_id!r}, "
                    f"as_of_date={as_of_date!r}: {exc}"
                ),
            ) from exc

        if not final_response.content:
            raise ToolLoopExhaustedError(
                _UPSTREAM_FAILURE_MESSAGE,
                detail=(
                    f"Fine summary generation failed: "
                    f"Model returned no summary "
                    f"for order_id={order_id!r}, as_of_date={as_of_date!r}."
                ),
            )

        if not isinstance(final_response.content, str):
            raise ToolLoopExhaustedError(
                _UPSTREAM_FAILURE_MESSAGE,
                detail=(
                    f"Fine summary generation failed: "
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
                "Fine summary heartbeat callback failed for order_id=%s; continuing generation",
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

    def _get_tier_bands(
        self,
        retailer_id: str,
        rule_id: str,
    ) -> dict[str, Any]:
        rules = self.rules.get_rules_for_retailer(retailer_id)
        rule = next(
            (rule for rule in rules if rule.rule_id == rule_id),
            None,
        )

        if rule is None:
            return {
                "rule_id": rule_id,
                "found": False,
            }

        return {
            "rule_id": rule.rule_id,
            "calc_type": rule.calc_type.value,
            "tiers": [
                {
                    "band_min": tier.band_min,
                    "band_max": tier.band_max,
                    "rate": tier.rate,
                }
                for tier in (rule.tiers or [])
            ],
        }
