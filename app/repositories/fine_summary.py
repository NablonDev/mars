"""Repository for persisted fine-summary generation jobs."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import FineSummary


def _to_dict(row: FineSummary) -> dict:
    return {
        "id": row.id,
        "order_id": row.order_id,
        "as_of_date": row.as_of_date,
        "agent_id": row.agent_id,
        "prompt_version": row.prompt_version,
        "context_hash": row.context_hash,
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
                FineSummary.status == "READY",
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
    ) -> dict:
        existing = self._find(order_id, as_of_date, prompt_version)

        if existing is not None:
            return self._reset_to_pending(existing, context_hash)

        row = FineSummary(
            order_id=order_id,
            as_of_date=as_of_date,
            agent_id=agent_id,
            prompt_version=prompt_version,
            context_hash=context_hash,
            status="PENDING",
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
            return self._reset_to_pending(existing, context_hash)

        return _to_dict(row)

    def _reset_to_pending(
        self,
        row: FineSummary,
        context_hash: str,
    ) -> dict:
        row.context_hash = context_hash
        row.status = "PENDING"
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

        row.status = "READY"
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

        row.status = "FAILED"
        row.error_message = error_message
        row.model_name = None
        row.summary = None
        self._session.flush()

        return _to_dict(row)
