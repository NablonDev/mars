"""Repository for `cmir.cmir_job_run_context`/`cmir_job_item_context` -- the
CMIR-specific extension tables over the shared `process.job_run`/
`process.job_item` queue (see `app.models.cmir.job_context` for why these
stay domain-owned and why the table names are prefixed).

`CmirJobItemContext` is shared by *both* the CMIR email-ingest pipeline
(`email_event_id` set) and the PO-validation pipeline
(`purchase_order_line_id` set) -- exactly one of the two, never both/neither
(DB-enforced by raw migration DDL on Postgres only, see that model's
docstring). This repository's `create()` guards the same invariant at the
application level, the same posture `WorkflowThreadRepository.create()`
already takes for its own two-nullable-FK pair.

Deferred by Phase 2 to Phase 3 (this phase): these models existed from
Phase 1 with no repository.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cmir.job_context import CmirJobItemContext, CmirJobRunContext


def _run_context_to_dict(row: CmirJobRunContext) -> dict:
    return {
        "job_run_id": row.job_run_id,
        "source_type": row.source_type,
        "external_batch_id": row.external_batch_id,
        "service_bus_topic": row.service_bus_topic,
        "service_bus_subscription": row.service_bus_subscription,
        "metadata_json": row.metadata_json,
    }


def _item_context_to_dict(row: CmirJobItemContext) -> dict:
    return {
        "job_item_id": row.job_item_id,
        "email_event_id": row.email_event_id,
        "purchase_order_line_id": row.purchase_order_line_id,
        "metadata_json": row.metadata_json,
    }


class CmirJobRunContextRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        job_run_id: UUID,
        source_type: str,
        external_batch_id: str | None = None,
        service_bus_topic: str | None = None,
        service_bus_subscription: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        row = CmirJobRunContext(
            job_run_id=job_run_id,
            source_type=source_type,
            external_batch_id=external_batch_id,
            service_bus_topic=service_bus_topic,
            service_bus_subscription=service_bus_subscription,
            metadata_json=metadata or {},
        )
        self._session.add(row)
        self._session.flush()
        return _run_context_to_dict(row)

    def get(self, job_run_id: UUID) -> dict | None:
        row = self._session.get(CmirJobRunContext, job_run_id)
        return _run_context_to_dict(row) if row is not None else None


class CmirJobItemContextRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        job_item_id: UUID,
        *,
        email_event_id: UUID | None = None,
        purchase_order_line_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        if (email_event_id is None) == (purchase_order_line_id is None):
            raise ValueError(
                "Exactly one of email_event_id/purchase_order_line_id must be set for a job item context."
            )

        row = CmirJobItemContext(
            job_item_id=job_item_id,
            email_event_id=email_event_id,
            purchase_order_line_id=purchase_order_line_id,
            metadata_json=metadata or {},
        )
        self._session.add(row)
        self._session.flush()
        return _item_context_to_dict(row)

    def get(self, job_item_id: UUID) -> dict | None:
        row = self._session.get(CmirJobItemContext, job_item_id)
        return _item_context_to_dict(row) if row is not None else None

    def get_by_email_event(self, email_event_id: UUID) -> dict | None:
        row = self._session.scalars(
            select(CmirJobItemContext).where(CmirJobItemContext.email_event_id == email_event_id)
        ).first()
        return _item_context_to_dict(row) if row is not None else None
