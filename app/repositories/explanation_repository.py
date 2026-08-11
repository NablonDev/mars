"""Repository for the persisted LLM-generated projection explanation
audit trail (`fact_projection_explanation`). `save` is insert-only, no
upsert -- unlike `ProjectionRepository.save_result` -- because silently
overwriting a past explanation would destroy the record of what was
actually shown to a client; a second write under the same
(order_id, as_of_date, prompt_version) key from the normal (non-force)
path must fail loudly (`IntegrityError`), not overwrite history.

`replace` is the one deliberate exception to that rule: it exists solely
for `ExplanationService.explain_order`'s `force_regenerate=True` path,
where overwriting the prior row under the same key *is* the explicitly
requested action, not an accident. See its docstring below."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ProjectionExplanation


def _to_dict(row: ProjectionExplanation) -> dict:
    return {
        "id": row.id,
        "order_id": row.order_id,
        "as_of_date": row.as_of_date,
        "prompt_version": row.prompt_version,
        "context_hash": row.context_hash,
        "model_name": row.model_name,
        "explanation": row.explanation_json,
        "created_at": row.created_at,
    }


class ExplanationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_cached(self, order_id: str, as_of_date: date, prompt_version: str) -> dict | None:
        row = self._session.scalars(
            select(ProjectionExplanation).where(
                ProjectionExplanation.order_id == order_id,
                ProjectionExplanation.as_of_date == as_of_date,
                ProjectionExplanation.prompt_version == prompt_version,
            )
        ).first()
        return _to_dict(row) if row is not None else None

    def save(
        self,
        order_id: str,
        as_of_date: date,
        prompt_version: str,
        context_hash: str,
        model_name: str,
        explanation: dict,
    ) -> dict:
        row = ProjectionExplanation(
            order_id=order_id,
            as_of_date=as_of_date,
            prompt_version=prompt_version,
            context_hash=context_hash,
            model_name=model_name,
            explanation_json=explanation,
        )
        self._session.add(row)
        self._session.flush()
        return _to_dict(row)

    def replace(
        self,
        order_id: str,
        as_of_date: date,
        prompt_version: str,
        context_hash: str,
        model_name: str,
        explanation: dict,
    ) -> dict:
        """Update-in-place if a row already exists for this
        (order_id, as_of_date, prompt_version) key, else insert. Used only
        by `ExplanationService.explain_order`'s `force_regenerate=True`
        path -- calling `save()` there would raise `IntegrityError` against
        an already-cached key, which is the one scenario the flag exists
        for. The invariant this repository protects is that *normal*
        writes never silently overwrite history, not that a row can never
        change once a caller has explicitly asked to regenerate it.

        If no row exists yet for this key, there's a narrow window where
        two concurrent force_regenerate calls can both take the insert
        branch and one loses the unique-constraint race -- that case is
        caught and retried once as an update rather than raised."""
        existing = self._session.scalars(
            select(ProjectionExplanation).where(
                ProjectionExplanation.order_id == order_id,
                ProjectionExplanation.as_of_date == as_of_date,
                ProjectionExplanation.prompt_version == prompt_version,
            )
        ).first()
        if existing is not None:
            existing.context_hash = context_hash
            existing.model_name = model_name
            existing.explanation_json = explanation
            self._session.flush()
            return _to_dict(existing)

        try:
            return self.save(
                order_id=order_id,
                as_of_date=as_of_date,
                prompt_version=prompt_version,
                context_hash=context_hash,
                model_name=model_name,
                explanation=explanation,
            )
        except IntegrityError:
            # Narrow race: two concurrent force_regenerate calls against a
            # brand-new key can both see `existing is None` above and both
            # take the insert branch -- only one insert wins the unique
            # constraint. Roll back this failed insert and retry once as
            # an update against the row the other call just committed,
            # rather than raising IntegrityError back out to the caller.
            self._session.rollback()
            existing = self._session.scalars(
                select(ProjectionExplanation).where(
                    ProjectionExplanation.order_id == order_id,
                    ProjectionExplanation.as_of_date == as_of_date,
                    ProjectionExplanation.prompt_version == prompt_version,
                )
            ).first()
            if existing is None:
                raise
            existing.context_hash = context_hash
            existing.model_name = model_name
            existing.explanation_json = explanation
            self._session.flush()
            return _to_dict(existing)
