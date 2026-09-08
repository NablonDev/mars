"""Post-delivery penalty dispute (chargeback resolution), `penalties.penalty_dispute`."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSONB_OR_JSON, PENALTIES_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


def generate_dispute_number() -> str:
    """Generate a human-legible dispute number, never accepted from a client."""
    return f"DSP-{uuid4().hex[:12].upper()}"


class PenaltyDispute(Base, TimestampMixin):
    """Post-delivery penalty dispute (chargeback resolution).

    Tracks retailer claims against actual penalties with analysis, verdict, and
    override tracking. Lifecycle: OPEN → ANALYZED → RESOLVED (or OVERRIDDEN).
    """

    __tablename__ = "penalty_dispute"
    __table_args__ = (
        CheckConstraint(
            "dispute_status IN ('OPEN', 'ANALYZED', 'RESOLVED', 'OVERRIDDEN')",
            name="ck_penalty_dispute_dispute_status",
        ),
        CheckConstraint(
            "verdict IS NULL OR verdict IN ('NO_PAY', 'PAY_PARTIAL', 'PAY_FULL')",
            name="ck_penalty_dispute_verdict",
        ),
        CheckConstraint(
            "override_verdict IS NULL OR override_verdict IN ('NO_PAY', 'PAY_PARTIAL', 'PAY_FULL')",
            name="ck_penalty_dispute_override_verdict",
        ),
        CheckConstraint(
            "reason_code IN ('AMOUNT_INCORRECT', 'NOT_LATE', 'QTY_CONFIRMED', 'RULE_MISAPPLIED', 'OTHER')",
            name="ck_penalty_dispute_reason_code",
        ),
        {"schema": PENALTIES_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    dispute_number: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, default=generate_dispute_number
    )
    actual_penalty_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey(f"{PENALTIES_SCHEMA}.actual_penalty.id")
    )
    purchase_order_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("purchase_order.id"))
    # Set once `analyze()` resolves the effective rule; NULL while OPEN.
    rule_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{PENALTIES_SCHEMA}.penalty_rule.id"), nullable=True
    )
    reason_code: Mapped[str] = mapped_column(String(50))
    claimed_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    # computed_amount/delta_amount/verdict are all NULL until analyze() runs.
    computed_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # claimed_amount - computed_amount. Positive means the retailer
    # overcharged against Mars's own rule (a normal dispute win). Negative
    # means it undercharged, recorded for audit only and never volunteered as
    # a reason to pay more.
    delta_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    dispute_status: Mapped[str] = mapped_column(String(30), default="OPEN")
    # Audit trail: which rule matched, the facts fed into the engine, which
    # calc branch fired, whether a cap was applied. Written once at analyze()
    # and never read back by the engine, only by the dispute summary agent and
    # by a human reviewing resolve() or override().
    analysis_breakdown: Mapped[dict | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free text, since this codebase has no user identity model to key on.
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    override_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
