"""Repository for `penalties.penalty_summary` -- the merged
LLM-generated summary table replacing what were two near-mirror tables,
`projection_summary` (was `app/repositories/fine_projection/summary.py`)
and `mitigation_summary` (was `app/repositories/fine_mitigation/summary.py`).
One repository now, `summary_type` (`PROJECTION`|`MITIGATION`|`DISPUTE`, see
`app.models.enums.SummaryType`) threaded through every method instead of
two (now three) copy-pasted classes.

**Real behavior change, not just a merge (forced by the new schema, not a
choice made here):** the old tables' uniqueness/identity was
`(order_id, as_of_date, prompt_version)`; the new
`uq_penalty_summary_po_type_date` constraint on `penalty_summary` is
`(purchase_order_id, summary_type, as_of_date)` -- it does **not** include
`agent_id` (there is no `prompt_version` column any more; `prompt_version`
merged into `process.agent`, see that model's docstring). Practically: at
most one summary row can exist per PO/type/date now, regardless of which
agent/prompt-version produced it -- generating with a new prompt version
overwrites the row for that date rather than adding a second one. Every
triple-keyed method below (the original PROJECTION/MITIGATION lifecycle,
`_find`/`get_cached`/`get_by_key`/`get_latest_not_after`/`create_pending`/
`mark_ready`/`find_reusable`/`create_reused`/`mark_failed`) is keyed
strictly on `(purchase_order_id, summary_type, as_of_date)`, unchanged from
before `penalty_dispute` existed; `agent_id` is recorded for provenance and
used as the reuse-eligibility filter that `prompt_version` used to serve.

`find_stranded_pending` (merges the old
`find_stranded_pending_projection_summaries`/
`find_stranded_pending_mitigation_summaries`, which lived on
`JobQueueRepository`) moved here because it needs both `PenaltySummary`
and `PenaltyJobItemContext` -- both `penalties`-schema concerns the
domain-agnostic `process.JobQueueRepository` should not import. Coverage
is now keyed on the *existence* of a matching `PenaltyJobItemContext` row
(1:1 with a `process.job_item`, so an existing context row always implies
an existing, non-deleted job item) rather than joining through
`process.job_item` itself -- same "any task type/status counts as
coverage" semantics as before.

**DISPUTE rows go through the exact same triple-keyed methods above as
PROJECTION/MITIGATION**, with `summary_type="DISPUTE"` passed as the
ordinary parameter every method already accepts. An earlier pass added a
`dispute_id` FK column plus a parallel set of `dispute_id`-keyed methods
(`get_by_dispute_id`/`get_cached_for_dispute`/`create_pending_for_dispute`/
`mark_ready_for_dispute`/`mark_failed_for_dispute`) so a DISPUTE row could
be looked up per-entity instead of per-triple -- reverted (see
`app.models.penalties.summary.PenaltySummary`'s module docstring for why,
and for the known limitation this reintroduces: a PO can have more than
one concurrent dispute, and two analyzed the same calendar day collide on
`(purchase_order_id, "DISPUTE", as_of_date)`).
`app.services.penalties.dispute.summary_service.DisputeSummaryService`
resolves a `dispute_id` to its `(purchase_order_id, as_of_date)` before
calling these methods, exactly as `ProjectionSummaryService`/
`MitigationSummaryService` do for their own domains.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import PenaltySummary
from app.models.enums import SummaryStatus
from app.models.penalties.job_context import PenaltyJobItemContext


def _to_dict(row: PenaltySummary) -> dict:
    return {
        "id": row.id,
        "purchase_order_id": row.purchase_order_id,
        "summary_type": row.summary_type,
        "as_of_date": row.as_of_date,
        "agent_id": row.agent_id,
        "context_hash": row.context_hash,
        "content_fingerprint": row.content_fingerprint,
        "source_as_of_date": row.source_as_of_date,
        "status": row.status,
        "model_name": row.model_name,
        "summary": row.summary,
        "error_message": row.error_message,
        "created_at": row.created_at,
    }


class PenaltySummaryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    # ------------------------------------------------------------------
    # PROJECTION / MITIGATION -- (purchase_order_id, summary_type,
    # as_of_date)-keyed, unchanged by DISPUTE's addition.
    # ------------------------------------------------------------------

    def _find(self, purchase_order_id: UUID, summary_type: str, as_of_date: date) -> PenaltySummary | None:
        return self._session.scalars(
            select(PenaltySummary).where(
                PenaltySummary.purchase_order_id == purchase_order_id,
                PenaltySummary.summary_type == summary_type,
                PenaltySummary.as_of_date == as_of_date,
            )
        ).first()

    def get_cached(self, purchase_order_id: UUID, summary_type: str, as_of_date: date) -> dict | None:
        row = self._session.scalars(
            select(PenaltySummary).where(
                PenaltySummary.purchase_order_id == purchase_order_id,
                PenaltySummary.summary_type == summary_type,
                PenaltySummary.as_of_date == as_of_date,
                PenaltySummary.status == SummaryStatus.READY,
            )
        ).first()
        return _to_dict(row) if row is not None else None

    def get_by_key(self, purchase_order_id: UUID, summary_type: str, as_of_date: date) -> dict | None:
        row = self._find(purchase_order_id, summary_type, as_of_date)
        return _to_dict(row) if row is not None else None

    def get_latest_not_after(
        self, purchase_order_id: UUID, summary_type: str, as_of_date: date
    ) -> dict | None:
        """Latest row of any status at or before as_of_date -- the same
        nearest-prior-date reasoning as find_reusable, for read callers
        (`SummaryServiceBase.get_status`) that fall back when no row is
        dated exactly as_of_date.

        Deliberately status-agnostic, not READY-only: a still-PENDING or
        FAILED job dated before "today" (e.g. one `run_generation` never
        picked up -- see the worker-dispatch gap this reasoning was found
        alongside) must still be surfaced as that job's real status, not
        silently reported as "no job exists" just because it never reached
        READY. Ordering by as_of_date alone (regardless of status) also
        means a more recent PENDING/FAILED row correctly wins over an older
        READY one -- the caller is polling for the most relevant job near
        this date, not specifically "the latest usable narrative" (that
        latter, fingerprint-matched case is what find_reusable is for)."""
        row = self._session.scalars(
            select(PenaltySummary)
            .where(
                PenaltySummary.purchase_order_id == purchase_order_id,
                PenaltySummary.summary_type == summary_type,
                PenaltySummary.as_of_date <= as_of_date,
            )
            .order_by(PenaltySummary.as_of_date.desc())
        ).first()
        return _to_dict(row) if row is not None else None

    def create_pending(
        self,
        purchase_order_id: UUID,
        summary_type: str,
        as_of_date: date,
        agent_id: UUID,
        context_hash: str,
        content_fingerprint: str | None = None,
    ) -> dict:
        existing = self._find(purchase_order_id, summary_type, as_of_date)

        if existing is not None:
            return self._reset_to_pending(existing, agent_id, context_hash, content_fingerprint)

        row = PenaltySummary(
            purchase_order_id=purchase_order_id,
            summary_type=summary_type,
            as_of_date=as_of_date,
            agent_id=agent_id,
            context_hash=context_hash,
            content_fingerprint=content_fingerprint,
            status=SummaryStatus.PENDING,
        )
        self._session.add(row)

        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            existing = self._find(purchase_order_id, summary_type, as_of_date)
            if existing is None:
                raise
            return self._reset_to_pending(existing, agent_id, context_hash, content_fingerprint)

        return _to_dict(row)

    def _reset_to_pending(
        self,
        row: PenaltySummary,
        agent_id: UUID,
        context_hash: str,
        content_fingerprint: str | None = None,
    ) -> dict:
        row.agent_id = agent_id
        row.context_hash = context_hash
        row.content_fingerprint = content_fingerprint
        # Re-arming starts a fresh generation and clears reuse lineage.
        row.source_as_of_date = None
        row.status = SummaryStatus.PENDING
        row.model_name = None
        row.summary = None
        row.error_message = None
        self._session.flush()
        return _to_dict(row)

    def mark_ready(
        self,
        purchase_order_id: UUID,
        summary_type: str,
        as_of_date: date,
        agent_id: UUID,
        model_name: str,
        summary: str,
        content_fingerprint: str | None = None,
    ) -> dict:
        row = self._find(purchase_order_id, summary_type, as_of_date)

        if row is None:
            row = PenaltySummary(
                purchase_order_id=purchase_order_id,
                summary_type=summary_type,
                as_of_date=as_of_date,
                agent_id=agent_id,
                context_hash="",
            )
            self._session.add(row)

        row.agent_id = agent_id
        row.status = SummaryStatus.READY
        row.model_name = model_name
        row.summary = summary
        row.error_message = None
        row.content_fingerprint = content_fingerprint
        # A fresh generation has no reuse source.
        row.source_as_of_date = None
        self._session.flush()

        return _to_dict(row)

    def find_reusable(
        self,
        purchase_order_id: UUID,
        summary_type: str,
        agent_id: UUID,
        content_fingerprint: str,
        earliest_source_date: date,
        *,
        not_after: date | None = None,
    ) -> dict | None:
        """Find the latest READY row with matching agent and content.

        The effective source date is `source_as_of_date`, falling back to
        `as_of_date` for a freshly generated row. `not_after` prevents
        reusing a summary generated after the requested date. `agent_id`
        is the reuse-eligibility filter `prompt_version` used to serve
        before `agent`/`prompt_version` merged (see this module's
        docstring).
        """
        effective_source_date = func.coalesce(PenaltySummary.source_as_of_date, PenaltySummary.as_of_date)

        conditions = [
            PenaltySummary.purchase_order_id == purchase_order_id,
            PenaltySummary.summary_type == summary_type,
            PenaltySummary.agent_id == agent_id,
            PenaltySummary.content_fingerprint == content_fingerprint,
            PenaltySummary.status == SummaryStatus.READY,
            effective_source_date >= earliest_source_date,
        ]
        if not_after is not None:
            conditions.append(effective_source_date <= not_after)

        row = self._session.scalars(
            select(PenaltySummary).where(*conditions).order_by(effective_source_date.desc())
        ).first()

        return _to_dict(row) if row is not None else None

    def create_reused(
        self,
        purchase_order_id: UUID,
        summary_type: str,
        as_of_date: date,
        agent_id: UUID,
        context_hash: str,
        content_fingerprint: str,
        model_name: str,
        summary: str,
        source_as_of_date: date,
    ) -> dict:
        """Create or update a READY row using an existing summary.

        `source_as_of_date` is the summary's original generation date,
        preserved across reuse chains.
        """
        existing = self._find(purchase_order_id, summary_type, as_of_date)

        if existing is not None:
            return self._apply_reused_fields(
                existing,
                agent_id=agent_id,
                context_hash=context_hash,
                content_fingerprint=content_fingerprint,
                model_name=model_name,
                summary=summary,
                source_as_of_date=source_as_of_date,
            )

        row = PenaltySummary(
            purchase_order_id=purchase_order_id,
            summary_type=summary_type,
            as_of_date=as_of_date,
            agent_id=agent_id,
            context_hash=context_hash,
            content_fingerprint=content_fingerprint,
            source_as_of_date=source_as_of_date,
            status=SummaryStatus.READY,
            model_name=model_name,
            summary=summary,
        )
        self._session.add(row)

        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            existing = self._find(purchase_order_id, summary_type, as_of_date)
            if existing is None:
                raise
            return self._apply_reused_fields(
                existing,
                agent_id=agent_id,
                context_hash=context_hash,
                content_fingerprint=content_fingerprint,
                model_name=model_name,
                summary=summary,
                source_as_of_date=source_as_of_date,
            )

        return _to_dict(row)

    def _apply_reused_fields(
        self,
        row: PenaltySummary,
        *,
        agent_id: UUID,
        context_hash: str,
        content_fingerprint: str,
        model_name: str,
        summary: str,
        source_as_of_date: date,
    ) -> dict:
        row.agent_id = agent_id
        row.context_hash = context_hash
        row.content_fingerprint = content_fingerprint
        row.source_as_of_date = source_as_of_date
        row.status = SummaryStatus.READY
        row.model_name = model_name
        row.summary = summary
        row.error_message = None
        self._session.flush()
        return _to_dict(row)

    def mark_failed(
        self,
        purchase_order_id: UUID,
        summary_type: str,
        as_of_date: date,
        agent_id: UUID,
        error_message: str,
    ) -> dict:
        row = self._find(purchase_order_id, summary_type, as_of_date)

        if row is None:
            row = PenaltySummary(
                purchase_order_id=purchase_order_id,
                summary_type=summary_type,
                as_of_date=as_of_date,
                agent_id=agent_id,
                context_hash="",
            )
            self._session.add(row)

        row.agent_id = agent_id
        row.status = SummaryStatus.FAILED
        row.error_message = error_message
        row.model_name = None
        row.summary = None
        self._session.flush()

        return _to_dict(row)

    # ------------------------------------------------------------------
    # Recovery sweep (see app.workers.fine_projection)
    # ------------------------------------------------------------------

    def find_stranded_pending(
        self,
        earliest_as_of_date: date,
        latest_as_of_date: date,
        summary_type: str,
    ) -> list[dict]:
        """Find pending summaries with no job_item context row for the
        same PO/date. Any task type counts as coverage, including terminal
        jobs -- this avoids creating a redundant *_SUMMARY_REGEN item
        alongside a live batch item for the same (PO, date)."""
        stmt = (
            select(PenaltySummary.purchase_order_id, PenaltySummary.as_of_date)
            .distinct()
            .outerjoin(
                PenaltyJobItemContext,
                (PenaltyJobItemContext.purchase_order_id == PenaltySummary.purchase_order_id)
                & (PenaltyJobItemContext.projection_date == PenaltySummary.as_of_date),
            )
            .where(
                PenaltySummary.summary_type == summary_type,
                PenaltySummary.status == SummaryStatus.PENDING,
                PenaltySummary.as_of_date >= earliest_as_of_date,
                PenaltySummary.as_of_date <= latest_as_of_date,
                PenaltyJobItemContext.job_item_id.is_(None),
            )
        )
        rows = self._session.execute(stmt).all()
        return [{"purchase_order_id": row.purchase_order_id, "as_of_date": row.as_of_date} for row in rows]

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def truncate_all(self) -> None:
        """Deletes every penalty_summary row (PROJECTION, MITIGATION, and
        DISPUTE), for a force-reseed. FKs to purchase_order only, so must
        run before `PurchaseOrderRepository.truncate_all()`."""
        self._session.execute(delete(PenaltySummary))
        self._session.flush()
