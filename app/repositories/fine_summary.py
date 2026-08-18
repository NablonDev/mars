"""Repository for persisted fine-summary generation jobs."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import FineSummary
from app.models.enums import SummaryStatus


def _to_dict(row: FineSummary) -> dict:
    return {
        "id": row.id,
        "order_id": row.order_id,
        "as_of_date": row.as_of_date,
        "agent_id": row.agent_id,
        "prompt_version": row.prompt_version,
        "context_hash": row.context_hash,
        "content_fingerprint": row.content_fingerprint,
        "source_as_of_date": row.source_as_of_date,
        "status": row.status,
        "model_name": row.model_name,
        "summary": row.summary,
        "error_message": row.error_message,
        "created_at": row.created_at,
    }


class FineSummaryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    def _find(
        self,
        order_id: str,
        as_of_date: date,
        prompt_version: str,
    ) -> FineSummary | None:
        return self._session.scalars(
            select(FineSummary).where(
                FineSummary.order_id == order_id,
                FineSummary.as_of_date == as_of_date,
                FineSummary.prompt_version == prompt_version,
            )
        ).first()

    def get_cached(
        self,
        order_id: str,
        as_of_date: date,
        prompt_version: str,
    ) -> dict | None:
        row = self._session.scalars(
            select(FineSummary).where(
                FineSummary.order_id == order_id,
                FineSummary.as_of_date == as_of_date,
                FineSummary.prompt_version == prompt_version,
                FineSummary.status == SummaryStatus.READY,
            )
        ).first()

        return _to_dict(row) if row is not None else None

    def get_by_key(
        self,
        order_id: str,
        as_of_date: date,
        prompt_version: str,
    ) -> dict | None:
        row = self._find(order_id, as_of_date, prompt_version)
        return _to_dict(row) if row is not None else None

    def create_pending(
        self,
        order_id: str,
        as_of_date: date,
        agent_id: UUID,
        prompt_version: str,
        context_hash: str,
        content_fingerprint: str | None = None,
    ) -> dict:
        existing = self._find(order_id, as_of_date, prompt_version)

        if existing is not None:
            return self._reset_to_pending(existing, context_hash, content_fingerprint)

        row = FineSummary(
            order_id=order_id,
            as_of_date=as_of_date,
            agent_id=agent_id,
            prompt_version=prompt_version,
            context_hash=context_hash,
            content_fingerprint=content_fingerprint,
            status=SummaryStatus.PENDING,
        )
        self._session.add(row)

        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            existing = self._find(
                order_id,
                as_of_date,
                prompt_version,
            )
            if existing is None:
                raise
            return self._reset_to_pending(existing, context_hash, content_fingerprint)

        return _to_dict(row)

    def _reset_to_pending(
        self,
        row: FineSummary,
        context_hash: str,
        content_fingerprint: str | None = None,
    ) -> dict:
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
        order_id: str,
        as_of_date: date,
        agent_id: UUID,
        prompt_version: str,
        model_name: str,
        summary: str,
        content_fingerprint: str | None = None,
    ) -> dict:
        row = self._find(order_id, as_of_date, prompt_version)

        if row is None:
            row = FineSummary(
                order_id=order_id,
                as_of_date=as_of_date,
                agent_id=agent_id,
                prompt_version=prompt_version,
                context_hash="",
            )
            self._session.add(row)

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
        order_id: str,
        prompt_version: str,
        content_fingerprint: str,
        earliest_source_date: date,
        *,
        not_after: date | None = None,
    ) -> dict | None:
        """Find the latest READY row with matching content and source date.

        The effective source date is `source_as_of_date`, falling back to
        `as_of_date` for a freshly generated row. `not_after` prevents
        reusing a narrative generated after the requested date.
        """
        effective_source_date = func.coalesce(FineSummary.source_as_of_date, FineSummary.as_of_date)

        conditions = [
            FineSummary.order_id == order_id,
            FineSummary.prompt_version == prompt_version,
            FineSummary.content_fingerprint == content_fingerprint,
            FineSummary.status == SummaryStatus.READY,
            effective_source_date >= earliest_source_date,
        ]
        if not_after is not None:
            conditions.append(effective_source_date <= not_after)

        row = self._session.scalars(
            select(FineSummary).where(*conditions).order_by(effective_source_date.desc())
        ).first()

        return _to_dict(row) if row is not None else None

    def create_reused(
        self,
        order_id: str,
        as_of_date: date,
        agent_id: UUID,
        prompt_version: str,
        context_hash: str,
        content_fingerprint: str,
        model_name: str,
        summary: str,
        source_as_of_date: date,
    ) -> dict:
        """Create or update a READY row using an existing narrative.

        `source_as_of_date` is the narrative's original generation date,
        preserved across reuse chains.
        """
        existing = self._find(order_id, as_of_date, prompt_version)

        if existing is not None:
            return self._apply_reused_fields(
                existing,
                context_hash=context_hash,
                content_fingerprint=content_fingerprint,
                model_name=model_name,
                summary=summary,
                source_as_of_date=source_as_of_date,
            )

        row = FineSummary(
            order_id=order_id,
            as_of_date=as_of_date,
            agent_id=agent_id,
            prompt_version=prompt_version,
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
            existing = self._find(order_id, as_of_date, prompt_version)
            if existing is None:
                raise
            return self._apply_reused_fields(
                existing,
                context_hash=context_hash,
                content_fingerprint=content_fingerprint,
                model_name=model_name,
                summary=summary,
                source_as_of_date=source_as_of_date,
            )

        return _to_dict(row)

    def _apply_reused_fields(
        self,
        row: FineSummary,
        *,
        context_hash: str,
        content_fingerprint: str,
        model_name: str,
        summary: str,
        source_as_of_date: date,
    ) -> dict:
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
        order_id: str,
        as_of_date: date,
        agent_id: UUID,
        prompt_version: str,
        error_message: str,
    ) -> dict:
        row = self._find(order_id, as_of_date, prompt_version)

        if row is None:
            row = FineSummary(
                order_id=order_id,
                as_of_date=as_of_date,
                agent_id=agent_id,
                prompt_version=prompt_version,
                context_hash="",
            )
            self._session.add(row)

        row.status = SummaryStatus.FAILED
        row.error_message = error_message
        row.model_name = None
        row.summary = None
        self._session.flush()

        return _to_dict(row)
