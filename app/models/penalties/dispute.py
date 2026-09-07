"""Post-delivery penalty dispute (chargeback resolution), `penalties.penalty_dispute`.

Adjudicates a `penalties.actual_penalty` charge already applied against a
purchase order: was the retailer's own charged amount correct, given what
Mars's own penalty rule computes from the *real, final* post-delivery facts
(actual confirmed quantity, actual ship/delivery dates) -- not the
probability-weighted estimate `penalty_projection` computed before delivery.

Modeled structurally after `common.po_delivery_change_request`
(`app.models.common.delivery_change_request.PoDeliveryChangeRequest`) -- the
codebase's existing "lifecycle record with a CHECK-constrained status plus a
resolution audit trail" shape -- but lives in the `penalties` schema (plain
FKs to `actual_penalty`/`penalty_rule`, both penalties-only concepts), not
`common`.

State machine (`dispute_status`): OPEN -> ANALYZED (the deterministic engine
in `app.services.penalties.dispute.engine` ran and persisted a verdict) ->
RESOLVED (a human accepts the verdict) or OVERRIDDEN (a human sets a
different verdict; `override_reason` required -- enforced at the schema/
service layer, not a DB CHECK, same posture as every other cross-field rule
in this codebase, e.g. `DeliveryChangeResponseRequest`'s
`countered_delivery_date`). RESOLVED/OVERRIDDEN are both terminal.

No hard unique constraint on `actual_penalty_id` alone: a retailer can amend
a charge, producing a second dispute cycle once the first is terminal --
"at most one OPEN/ANALYZED dispute per charge" is enforced in
`app.services.penalties.dispute.service.DisputeService.open_dispute`
instead (see that method's docstring), the same posture
`PoDeliveryChangeRequestService.create_request` already uses for "at most
one active request per PO" (checked via
`PoDeliveryChangeRequestRepository.find_active_for_purchase_order`, not a DB
constraint).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSONB_OR_JSON, PENALTIES_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


def generate_dispute_number() -> str:
    """Human-legible business id, same "generated, never client-supplied"
    posture as `PoDeliveryChangeRequest.request_id`
    (`app.models.common.delivery_change_request.generate_request_id`)."""
    return f"DSP-{uuid4().hex[:12].upper()}"


class PenaltyDispute(Base, TimestampMixin):
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
    # claimed_amount - computed_amount: positive means the retailer
    # overcharged relative to Mars's own rule (a normal dispute win);
    # negative means the retailer undercharged -- recorded for audit only,
    # never volunteered as a reason to pay more (see the engine's
    # module docstring).
    delta_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    dispute_status: Mapped[str] = mapped_column(String(30), default="OPEN")
    # Audit trail: which rule matched, the real facts fed into the engine,
    # which calc branch fired, whether a cap was applied. Never read back by
    # the engine itself -- write-once at analyze(), read by the dispute
    # summary agent and by a human reviewing resolve()/override().
    analysis_breakdown: Mapped[dict | None] = mapped_column(JSONB_OR_JSON, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free text -- no auth/user table in this codebase (see
    # PoDeliveryChangeRequest's precedent of a similarly free-text audit
    # field where no user identity model exists).
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    override_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
