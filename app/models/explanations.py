"""Persisted audit trail of natural-language projection explanations
actually shown to a client -- see app/services/explanation_service.py.

Keyed on (order_id, as_of_date, prompt_version), not just (order_id,
as_of_date): a prompt-version bump is a deliberate content/formula-
wording change, and serving an old row under a new prompt version would
look exactly like a caching bug to whoever debugs a client complaint
later. `context_hash` is diagnostic only (sha256 of the mandatory-context
JSON) -- it is intentionally not part of the uniqueness key; two
different hashes under the same key would be a bug to investigate, not a
reason to allow a second row.

`explanation_json` uses the generic SQLAlchemy `JSON` type (not
Postgres-only `JSONB`) so tests/test_migration_parity.py keeps running
against SQLite.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, generate_uuid7


class ProjectionExplanation(Base):
    __tablename__ = "fact_projection_explanation"
    __table_args__ = (
        UniqueConstraint(
            "order_id", "as_of_date", "prompt_version", name="uq_projection_explanation_order_date_prompt"
        ),
    )
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey("fact_order.order_id"))
    as_of_date: Mapped[date] = mapped_column(Date)
    prompt_version: Mapped[str] = mapped_column(String(20))
    context_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex digest, diagnostic only
    model_name: Mapped[str] = mapped_column(String(100))
    explanation_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
