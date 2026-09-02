"""Shared `get_or_schedule`/reuse-check/cache-hit-miss/PENDING->READY|FAILED
lifecycle for the two penalty-summary LLM features (projection and
mitigation summaries).

Was two near-mirror implementations, `app/services/fine_projection/summary.py`
(`FineProjectionSummaryService`) and `app/services/fine_mitigation/summary.py`
(`FineMitigationSummaryService`) -- their documented duplication (see both
modules' former docstrings) is merged here per the approved plan's Phase 2
flag #3. `app.services.penalties.projection.summary_service`/
`app.services.penalties.mitigation.summary_service` now hold only the
genuinely different pieces: which repositories back the domain-specific
history/context, which agent/prompt content, and how the mandatory context
and content fingerprint are assembled.

Both features now read/write the single merged `penalties.penalty_summary`
table (`app/repositories/penalties/summary.py`) instead of two separate
tables, keyed by the `summary_type` discriminator
(`app.models.enums.SummaryType`).

This module does NOT import `app.agents.*` beyond the provider client and
tool base type -- prompt content, tool schemas, and the domain Pydantic
context/output models stay owned by each domain's `app/agents/penalties/
<domain>/` package. The bounded tool-calling loop itself (previously inline
here, in `_run_tool_loop`) now lives in that package's `agent.py`
(`PenaltyProjectionAgent`/`PenaltyMitigationAgent`, Phase 4) -- this class
only assembles context/tools and delegates generation via the `_generate`
hook, which each subclass implements to call its own agent's differently
named public method (`generate_projection_summary`/
`generate_mitigation_summary`).

Per-instance `get_or_schedule` enqueues a `process.job_run`/`job_item` row
(via `JobQueueRepository`) and attaches the matching
`penalties.penalty_job_item_context` row in the same transaction, so a
worker has something real to dequeue and `run_generation` against, and so
`PenaltySummaryRepository.find_stranded_pending`'s recovery sweep (which
looks for a PENDING summary with *no* matching job-item-context row) stays
meaningful. See this phase's report for what was deliberately left out of
this wiring (`ProjectionService.run_for_all_open`/
`MitigationService.run_for_purchase_order` stay synchronous, unenqueued).
"""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import ClassVar
from uuid import UUID

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.agents.penalties._summary_output import PenaltySummaryOutputBase
from app.agents.providers.azure_openai import AzureOpenAIChatClient
from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.models.enums import JobRunType, SummaryStatus
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.penalties.job_context import PenaltyJobItemContextRepository
from app.repositories.penalties.summary import PenaltySummaryRepository
from app.repositories.process.agent_registry import AgentRegistryRepository
from app.repositories.process.job_queue import JobQueueRepository

#: `NotFoundError(code=...)` for `get_status` when no summary job row exists
#: at all -- keyed by `summary_domain` ("projection" | "mitigation").
_NO_SUMMARY_JOB_CODES: dict[str, str] = {
    "projection": "NO_PROJECTION_SUMMARY_JOB_EXISTS",
    "mitigation": "NO_MITIGATION_SUMMARY_JOB_EXISTS",
}


@dataclass
class SummaryJob[OutputT: BaseModel]:
    purchase_order_id: UUID
    as_of_date: date
    status: str
    output: OutputT | None = None
    error_message: str | None = None


logger = logging.getLogger(__name__)


class SummaryServiceBase[ContextT: BaseModel, OutputT: BaseModel](ABC):
    """Shared orchestration for a penalty-summary LLM feature.

    Subclasses set the class-level identity attributes below and implement
    the domain-specific hooks; everything else (caching, reuse, the
    PENDING/READY/FAILED lifecycle) lives here, once. The bounded
    tool-calling loop itself lives one layer down, in each subclass's
    `PenaltyProjectionAgent`/`PenaltyMitigationAgent` (see `_generate`).
    """

    #: `SummaryType.PROJECTION` | `SummaryType.MITIGATION` -- the
    #: `penalty_summary.summary_type` discriminator this instance reads/writes.
    summary_type: ClassVar[str]
    #: Keys `_NO_SUMMARY_JOB_CODES` / each agent's upstream-failure-code map
    #: -- "projection" | "mitigation".
    summary_domain: ClassVar[str]
    #: `process.agent.agent_code` this feature registers/reads under.
    agent_code: ClassVar[str]
    agent_name: ClassVar[str]
    prompt_version: ClassVar[str]
    system_prompt: ClassVar[str]
    #: `process.job_item.item_type` / `penalty_job_item_context.task_type`
    #: used when `get_or_schedule` enqueues a regeneration job.
    job_task_type: ClassVar[str]
    upstream_failure_message: ClassVar[str]

    def __init__(
        self,
        *,
        purchase_orders: PurchaseOrderRepository,
        summaries: PenaltySummaryRepository,
        agent_registry: AgentRegistryRepository,
        job_queue: JobQueueRepository,
        job_context: PenaltyJobItemContextRepository,
        llm: AzureOpenAIChatClient,
    ) -> None:
        self.purchase_orders = purchase_orders
        self.summaries = summaries
        self.agent_registry = agent_registry
        self.job_queue = job_queue
        self.job_context = job_context
        self.llm = llm

    # ------------------------------------------------------------------
    # Hooks every subclass must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def _validate(self, purchase_order_id: UUID, as_of_date: date | None) -> tuple[dict, date, list[dict]]:
        """Resolve `as_of_date`, confirm the purchase order and its
        domain-specific history exist and are in range, and return
        `(purchase_order, as_of_date, history)`. Raises
        `NotFoundError(code="PO_NOT_FOUND")` /
        `BusinessRuleError(code="NO_PROJECTION_EXISTS")` /
        `BusinessRuleError(code="NO_MITIGATION_OPTIONS_EXIST")` /
        `ValidationError(code="INVALID_AS_OF_DATE")` as appropriate."""

    @abstractmethod
    def _history_for_generation(self, purchase_order_id: UUID, as_of_date: date) -> list[dict]:
        """Like `_validate`'s history, but for `run_generation` -- no
        date-range validation (the job that reaches here was already
        validated once, at schedule time)."""

    @abstractmethod
    def _assemble_mandatory_context(
        self, purchase_order: dict, as_of_date: date, history: list[dict]
    ) -> ContextT:
        """Build the Pydantic context object handed to the LLM as the
        mandatory (non-tool-fetched) data."""

    @abstractmethod
    def _compute_content_fingerprint(self, context: ContextT) -> str:
        """Hash only the facts that determine the generated narrative."""

    @abstractmethod
    def _build_tools(self, purchase_order: dict, as_of_date: date) -> list[BaseTool]:
        """Build the bounded tool set for this generation call."""

    @abstractmethod
    def _output_with_reuse_cls(self) -> type[OutputT]:
        """The domain's `*Output` class extended with the shared reuse
        fields (`is_reused`, `generated_for_date`, `unchanged_since`,
        `unchanged_for_days`)."""

    @abstractmethod
    def _generate(
        self,
        context: ContextT,
        *,
        order_id: str,
        as_of_date: date,
        tools: list[BaseTool],
        heartbeat: Callable[[], None] | None,
    ) -> PenaltySummaryOutputBase:
        """Delegate to this feature's agent (`PenaltyProjectionAgent`/
        `PenaltyMitigationAgent`) -- calls its differently-named public
        method (`generate_projection_summary`/`generate_mitigation_summary`)
        to run the bounded tool-calling loop and produce the final output.

        Returns the plain (non-reuse-aware) output -- `run_generation` only
        needs `model_name`/`summary` off of it; the reuse fields on `OutputT`
        are assembled later, in `_to_output`, from the persisted row."""

    # ------------------------------------------------------------------
    # Shared lifecycle
    # ------------------------------------------------------------------

    def _ensure_registered(self) -> UUID:
        return self.agent_registry.ensure_registered(
            agent_code=self.agent_code,
            prompt_version=self.prompt_version,
            system_prompt=self.system_prompt,
            agent_name=self.agent_name,
            domain="penalties",
        )

    def get_or_schedule(
        self,
        purchase_order_id: UUID,
        as_of_date: date | None = None,
        force_regenerate: bool = False,
    ) -> SummaryJob[OutputT]:
        purchase_order, as_of_date, history = self._validate(purchase_order_id, as_of_date)

        context = self._assemble_mandatory_context(purchase_order, as_of_date, history)
        context_hash = hashlib.sha256(
            json.dumps(context.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        content_fingerprint = self._compute_content_fingerprint(context)
        logger.info(
            "%s summary content fingerprint: purchase_order_id=%s as_of_date=%s fingerprint=%s",
            self.summary_type,
            purchase_order_id,
            as_of_date,
            content_fingerprint,
        )

        if not force_regenerate:
            cached = self.summaries.get_cached(purchase_order_id, self.summary_type, as_of_date)
            if cached is not None:
                return SummaryJob(purchase_order_id, as_of_date, SummaryStatus.READY, self._to_output(cached))

            if get_settings().summary.reuse_enabled:
                reused = self._try_reuse(purchase_order_id, as_of_date, content_fingerprint, context_hash)
                if reused is not None:
                    return SummaryJob(
                        purchase_order_id, as_of_date, SummaryStatus.READY, self._to_output(reused)
                    )

        agent_id = self._ensure_registered()
        self.summaries.create_pending(
            purchase_order_id=purchase_order_id,
            summary_type=self.summary_type,
            as_of_date=as_of_date,
            agent_id=agent_id,
            context_hash=context_hash,
            content_fingerprint=content_fingerprint,
        )
        self._enqueue_regeneration_job(purchase_order_id, as_of_date, force_regenerate)
        self.summaries.commit()

        return SummaryJob(purchase_order_id, as_of_date, SummaryStatus.PENDING)

    def _enqueue_regeneration_job(
        self, purchase_order_id: UUID, as_of_date: date, force_regenerate: bool
    ) -> None:
        """Create a `process.job_run`/`job_item` for a worker to later pick
        up and call `run_generation` against, with the matching
        `penalty_job_item_context` row attached in the same transaction.
        """
        settings = get_settings()
        run = self.job_queue.create_run(
            job_type=self.job_task_type,
            trigger_type=JobRunType.ON_DEMAND,
            requested_item_count=1,
        )
        dedupe_key = f"{purchase_order_id}:{as_of_date.isoformat()}:{self.summary_type}"
        item = self.job_queue.enqueue(
            run["id"],
            item_type=self.job_task_type,
            dedupe_key=dedupe_key,
            max_attempts=settings.job_queue.max_attempts,
        )
        if item is None:
            return
        # `enqueue()` returns the existing in-flight row, not a new one,
        # when `dedupe_key` is already PENDING/RUNNING (e.g. a second
        # get_or_schedule call for the same PO/date/type while the first
        # job is still unclaimed) -- a context row already exists for that
        # job_item_id in that case, and creating a second one would violate
        # its primary key.
        if self.job_context.get(item["id"]) is not None:
            return
        self.job_context.create(
            job_item_id=item["id"],
            purchase_order_id=purchase_order_id,
            projection_date=as_of_date,
            task_type=self.job_task_type,
            force_regenerate_summary=force_regenerate,
        )

    def _try_reuse(
        self,
        purchase_order_id: UUID,
        as_of_date: date,
        content_fingerprint: str,
        context_hash: str,
    ) -> dict | None:
        """Reuse the latest matching narrative within the configured window."""
        settings = get_settings()
        earliest_source_date = as_of_date - timedelta(days=settings.summary.max_reuse_days)
        agent_id = self._ensure_registered()

        candidate = self.summaries.find_reusable(
            purchase_order_id=purchase_order_id,
            summary_type=self.summary_type,
            agent_id=agent_id,
            content_fingerprint=content_fingerprint,
            earliest_source_date=earliest_source_date,
            not_after=as_of_date,
        )
        if candidate is None:
            return None

        original_source_date = candidate["source_as_of_date"] or candidate["as_of_date"]

        reused = self.summaries.create_reused(
            purchase_order_id=purchase_order_id,
            summary_type=self.summary_type,
            as_of_date=as_of_date,
            agent_id=agent_id,
            context_hash=context_hash,
            content_fingerprint=content_fingerprint,
            model_name=candidate["model_name"],
            summary=candidate["summary"],
            source_as_of_date=original_source_date,
        )
        self.summaries.commit()

        logger.info(
            "%s summary reused: purchase_order_id=%s as_of_date=%s source_as_of_date=%s fingerprint=%s",
            self.summary_type,
            purchase_order_id,
            as_of_date,
            original_source_date,
            content_fingerprint,
        )

        return reused

    def get_status(self, purchase_order_id: UUID, as_of_date: date | None = None) -> SummaryJob[OutputT]:
        _, as_of_date, _ = self._validate(purchase_order_id, as_of_date)

        row = self.summaries.get_by_key(purchase_order_id, self.summary_type, as_of_date)
        if row is None:
            # No job dated exactly as_of_date -- fall back to the nearest
            # prior job of any status, same reasoning as find_reusable's
            # nearest-prior-date matching. Status-agnostic on purpose: a
            # PENDING/FAILED job dated before as_of_date must still be
            # reported as such, not treated as if it never existed just
            # because it never reached READY (see
            # PenaltySummaryRepository.get_latest_not_after's docstring).
            row = self.summaries.get_latest_not_after(purchase_order_id, self.summary_type, as_of_date)
        if row is None:
            raise NotFoundError(
                code=_NO_SUMMARY_JOB_CODES[self.summary_domain],
                message=(
                    f"No penalty-{self.summary_domain}-summary job found for "
                    f"purchase_order_id={purchase_order_id}, as_of_date={as_of_date.isoformat()} -- "
                    f"POST /penalties/{self.summary_domain}s/summary first."
                ),
            )

        output = self._to_output(row) if row["summary"] is not None else None

        return SummaryJob(
            purchase_order_id=purchase_order_id,
            as_of_date=row["as_of_date"],
            status=row["status"],
            output=output,
            error_message=row["error_message"],
        )

    def _to_output(self, row: dict) -> OutputT:
        source_as_of_date = row.get("source_as_of_date")
        is_reused = source_as_of_date is not None
        as_of_date = row["as_of_date"]

        output_cls = self._output_with_reuse_cls()
        return output_cls(
            order_id=str(row["purchase_order_id"]),
            as_of_date=as_of_date,
            prompt_version=self.prompt_version,
            model_name=row["model_name"],
            summary=row["summary"],
            is_reused=is_reused,
            generated_for_date=source_as_of_date if is_reused else as_of_date,
            unchanged_since=source_as_of_date if is_reused else None,
            unchanged_for_days=(as_of_date - source_as_of_date).days if is_reused else None,
        )

    def run_generation(
        self,
        purchase_order_id: UUID,
        as_of_date: date,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        """Generate the summary and persist success or failure.

        Failures are persisted and re-raised so queue callers can apply
        their retry/dead-letter policy. Heartbeats are best-effort and
        never abort generation.
        """
        purchase_order = self.purchase_orders.get_purchase_order(purchase_order_id)
        if purchase_order is None:
            logger.error(
                "%s summary job: purchase_order %s no longer exists",
                self.summary_type,
                purchase_order_id,
            )
            return

        try:
            history = self._history_for_generation(purchase_order_id, as_of_date)
            context = self._assemble_mandatory_context(purchase_order, as_of_date, history)
            content_fingerprint = self._compute_content_fingerprint(context)
            logger.info(
                "%s summary content fingerprint: purchase_order_id=%s as_of_date=%s fingerprint=%s",
                self.summary_type,
                purchase_order_id,
                as_of_date,
                content_fingerprint,
            )
            tools = self._build_tools(purchase_order, as_of_date)
            output = self._generate(
                context,
                order_id=str(purchase_order_id),
                as_of_date=as_of_date,
                tools=tools,
                heartbeat=heartbeat,
            )
        except ExternalServiceError as exc:
            logger.error(
                "%s summary generation failed: purchase_order_id=%s date=%s: %s",
                self.summary_type,
                purchase_order_id,
                as_of_date,
                exc.details or exc.message,
            )
            self._safe_mark_failed(purchase_order_id, as_of_date, exc.message)
            raise
        except Exception:
            logger.exception(
                "%s summary generation crashed: purchase_order_id=%s date=%s",
                self.summary_type,
                purchase_order_id,
                as_of_date,
            )
            self._safe_mark_failed(purchase_order_id, as_of_date, self.upstream_failure_message)
            raise

        self.summaries.mark_ready(
            purchase_order_id=purchase_order_id,
            summary_type=self.summary_type,
            as_of_date=as_of_date,
            agent_id=self._ensure_registered(),
            model_name=output.model_name,
            summary=output.summary,
            content_fingerprint=content_fingerprint,
        )

    def _safe_mark_failed(self, purchase_order_id: UUID, as_of_date: date, error_message: str) -> None:
        try:
            self.summaries.mark_failed(
                purchase_order_id,
                self.summary_type,
                as_of_date,
                self._ensure_registered(),
                error_message,
            )
        except Exception:
            logger.exception(
                "Failed to persist %s summary failure: purchase_order_id=%s date=%s",
                self.summary_type,
                purchase_order_id,
                as_of_date,
            )

    # The bounded tool-calling loop itself lives in each domain's
    # `PenaltyProjectionAgent`/`PenaltyMitigationAgent` (see `_generate`
    # above) -- Phase 4 extraction, was inline here as `_run_tool_loop`.
