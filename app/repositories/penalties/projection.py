"""Repository for `penalties.penalty_projection` (the daily snapshot
history a projection trend is plotted from) and `penalties.actual_penalty`
(the post-delivery outcome recorded against a PO). Was
`app/repositories/fine_projection/projection.py` plus the
`add_actual_fine`/`list_actual_fines` methods that used to live on
`app/repositories/order.py`'s `OrderRepository` -- both are penalty-domain
outcome facts keyed by `purchase_order_id`, grouped here rather than in
`common/purchase_order.py`.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ActualPenalty, PenaltyProjection, PurchaseOrder, Retailer
from app.services.penalties.projection import ProjectionResult


def _projection_to_dict(row: PenaltyProjection) -> dict:
    return {
        "id": row.id,
        "purchase_order_id": row.purchase_order_id,
        "rule_id": row.rule_id,
        "projection_date": row.projection_date,
        "violation_type": row.violation_type,
        "failure_probability": float(row.failure_probability),
        "penalty_amount": float(row.penalty_amount),
        "expected_penalty_amount": float(row.expected_penalty_amount),
        "days_to_delivery": row.days_to_delivery,
        "projection_status": row.projection_status,
    }


def _actual_penalty_to_dict(row: ActualPenalty) -> dict:
    return {
        "id": row.id,
        "actual_penalty_number": row.actual_penalty_number,
        "purchase_order_id": row.purchase_order_id,
        "violation_type": row.violation_type,
        "actual_penalty_amount": float(row.actual_penalty_amount),
        "invoice_or_deduction_date": row.invoice_or_deduction_date,
        "dispute_status": row.dispute_status,
    }


class PenaltyProjectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_result(self, purchase_order_id: UUID, result: ProjectionResult) -> dict[str, UUID]:
        """Persist every violation as its own `penalty_projection` row and
        return each one's surrogate id, keyed by the (stringified) `rule_id`
        that produced it -- `ProjectionService.run_for_purchase_order` uses
        this to stitch the persisted id back onto each `ViolationProjection`
        for the API response (`POST /penalties/projections` has no other
        way to hand a client that row's own id -- see `ViolationProjection.id`'s
        docstring)."""
        # `row.id` is a Python-side column default (`generate_uuid7`) --
        # SQLAlchemy only evaluates it during flush, so a newly-added row's
        # `id` reads back as `None` until after `self._session.flush()`
        # below runs. Collect (rule_id, row) pairs during the loop and read
        # `row.id` only afterward.
        rows_by_rule_id: list[tuple[str, PenaltyProjection]] = []
        for v in result.violations:
            # Deliberate Phase 3 fix (flagged in the phase report): `v.rule_id`
            # is the pure-engine `PenaltyRule.rule_id`, typed `str` (its contract
            # doesn't change -- see app.services.penalties.projection.types's
            # module docstring), but it now holds a stringified UUID (the
            # surrogate `penalty_rule.id` this column FKs to, not the business
            # `rule_code`). SQLAlchemy's `Uuid` column type does not coerce a
            # plain `str` bind value the way it does a `uuid.UUID` instance
            # (fails with `AttributeError: 'str' object has no attribute
            # 'hex'` at the DB layer) -- normalize once, here, at the one
            # place this repository accepts the value from the engine.
            rule_id = v.rule_id if isinstance(v.rule_id, UUID) else UUID(v.rule_id)
            existing = self._session.scalars(
                select(PenaltyProjection).where(
                    PenaltyProjection.purchase_order_id == purchase_order_id,
                    PenaltyProjection.rule_id == rule_id,
                    PenaltyProjection.projection_date == result.projection_date,
                )
            ).first()

            if existing is not None:
                existing.violation_type = v.violation_type
                existing.failure_probability = v.probability
                existing.penalty_amount = v.penalty_amount
                existing.expected_penalty_amount = v.expected_penalty_amount
                existing.days_to_delivery = result.days_to_delivery
                existing.projection_status = "OPEN"
                row = existing
            else:
                row = PenaltyProjection(
                    purchase_order_id=purchase_order_id,
                    rule_id=rule_id,
                    projection_date=result.projection_date,
                    violation_type=v.violation_type,
                    failure_probability=v.probability,
                    penalty_amount=v.penalty_amount,
                    expected_penalty_amount=v.expected_penalty_amount,
                    days_to_delivery=result.days_to_delivery,
                    projection_status="OPEN",
                )
                self._session.add(row)
            rows_by_rule_id.append((str(rule_id), row))
        self._session.flush()
        return {rule_id_str: row.id for rule_id_str, row in rows_by_rule_id}

    def get_by_id(self, projection_id: UUID) -> dict | None:
        """Fetch one `penalty_projection` row by its own surrogate id.

        Phase 7a addition (flagged -- repositories were nominally out of
        scope for that phase): `GET /penalties/projections/{projection_id}`
        has no other way to resolve a single projection row; every other
        method here is keyed by `purchase_order_id`, not by this table's
        own `id`. Purely additive, zero behavior change to any existing
        method."""
        row = self._session.get(PenaltyProjection, projection_id)
        return _projection_to_dict(row) if row is not None else None

    def list_projections(
        self,
        purchase_order_id: UUID | None = None,
        status: str | None = None,
        projection_date: date | None = None,
        projection_date_from: date | None = None,
        projection_date_to: date | None = None,
    ) -> list[dict]:
        """General `penalty_projection` row filter backing
        `GET /penalties/projections?purchase_order_id=&status=&
        projection_date=&projection_date_from=&projection_date_to=` -- the
        merged replacement for what used to be two separate routes:
        `GET .../penalty-projections` history (filtered only by
        `purchase_order_id`, via this method's own former `list_history`,
        now a thin wrapper below) and `GET /penalty-projections?status=`
        cross-PO (restricted to each PO's own latest `projection_date`; see
        `ProjectionService.list_open_across_purchase_orders`, now folded in
        here instead of composed from per-PO `get_latest` calls).

        `purchase_order_id` given: every matching row for that PO, in the
        same order `list_history` always returned them -- `status`/date
        filters are additive on top, with no latest-only restriction (a
        single PO's full history is still the full history).

        `purchase_order_id` omitted (the old cross-PO `?status=` list):
        restrict to each purchase order's own latest matching
        `projection_date` first, THEN apply `status` -- not the other way
        around, so a `status` value that only ever matches an older,
        non-latest row for some PO still excludes that PO here, exactly as
        `list_open_across_purchase_orders`'s per-PO `get_latest` composition
        did before.
        """
        query = select(PenaltyProjection)
        if purchase_order_id is not None:
            query = query.where(PenaltyProjection.purchase_order_id == purchase_order_id)
        if projection_date is not None:
            query = query.where(PenaltyProjection.projection_date == projection_date)
        if projection_date_from is not None:
            query = query.where(PenaltyProjection.projection_date >= projection_date_from)
        if projection_date_to is not None:
            query = query.where(PenaltyProjection.projection_date <= projection_date_to)
        if purchase_order_id is not None and status is not None:
            query = query.where(PenaltyProjection.projection_status == status)
        query = query.order_by(
            PenaltyProjection.projection_date.asc(),
            PenaltyProjection.rule_id.asc(),
        )
        rows = [_projection_to_dict(r) for r in self._session.scalars(query).all()]

        if purchase_order_id is None:
            latest_by_po: dict[UUID, date] = {}
            for row in rows:
                po_id = row["purchase_order_id"]
                if po_id not in latest_by_po or row["projection_date"] > latest_by_po[po_id]:
                    latest_by_po[po_id] = row["projection_date"]
            rows = [r for r in rows if r["projection_date"] == latest_by_po[r["purchase_order_id"]]]
            if status is not None:
                rows = [r for r in rows if r["projection_status"] == status]

        return rows

    def list_history(self, purchase_order_id: UUID) -> list[dict]:
        """Full, unfiltered projection history for one PO -- every other
        repository/service caller in this codebase still uses this narrow
        shape (`MitigationService`, `ProjectionSummaryService`, `get_latest`
        below, several tests); kept as its own method rather than inlining
        `list_projections(purchase_order_id=...)` at every call site."""
        return self.list_projections(purchase_order_id=purchase_order_id)

    def _get_stacking_mode(self, purchase_order_id: UUID) -> str:
        """Resolve the retailer's `stacking_mode` for a purchase order,
        the same lookup `ProjectionService.run_for_purchase_order`/
        `MitigationService._build_projection_result` perform via
        `PurchaseOrderRepository.get_purchase_order` +
        `MasterDataRepository.get_stacking_mode` -- done here as a single
        join instead of composing those two repositories, since no
        repository in this codebase depends on another (session-only
        constructors throughout; see `app/repositories/common/master_data.py`
        and `app/repositories/common/purchase_order.py`).

        Falls back to `"SUM"` if the purchase order or retailer can't be
        resolved (should not happen given the FK from `penalty_projection`
        to `purchase_order`), matching `MasterDataRepository.get_stacking_
        mode`'s own fallback for a retailer row that can't be found.
        """
        stacking_mode = self._session.scalars(
            select(Retailer.stacking_mode)
            .join(PurchaseOrder, PurchaseOrder.retailer_id == Retailer.id)
            .where(PurchaseOrder.id == purchase_order_id)
        ).first()
        return stacking_mode or "SUM"

    def get_latest(self, purchase_order_id: UUID) -> dict | None:
        history = self.list_history(purchase_order_id)
        if not history:
            return None

        latest_date = max(h["projection_date"] for h in history)
        rows = [h for h in history if h["projection_date"] == latest_date]

        stacking_mode = self._get_stacking_mode(purchase_order_id)
        if stacking_mode == "MAX":
            total = max((r["expected_penalty_amount"] for r in rows), default=0.0)
        else:
            total = sum(r["expected_penalty_amount"] for r in rows)

        return {
            "purchase_order_id": purchase_order_id,
            "projection_date": latest_date,
            "total_expected_penalty_amount": round(total, 2),
            "violations": rows,
        }

    def truncate_all(self) -> None:
        """Deletes every penalty_projection row, for a force-reseed. FKs to
        both purchase_order and penalty_rule, so must run before
        `PurchaseOrderRepository.truncate_all()`/`PenaltyRuleRepository.
        truncate_all()` clear either. Deferred by Phase 2 to this phase --
        seeding (Phase 3) cannot force-reseed without it; see the sibling
        `ActualPenaltyRepository.truncate_all()` below for the same gap on
        `actual_penalty`."""
        self._session.execute(delete(PenaltyProjection))
        self._session.flush()


class ActualPenaltyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_actual_penalty(
        self,
        actual_penalty_number: str,
        purchase_order_id: UUID,
        violation_type: str,
        actual_penalty_amount: float,
        invoice_or_deduction_date: date,
        dispute_status: str = "NONE",
    ) -> dict:
        row = ActualPenalty(
            actual_penalty_number=actual_penalty_number,
            purchase_order_id=purchase_order_id,
            violation_type=violation_type,
            actual_penalty_amount=actual_penalty_amount,
            invoice_or_deduction_date=invoice_or_deduction_date,
            dispute_status=dispute_status,
        )
        self._session.add(row)
        self._session.flush()
        return _actual_penalty_to_dict(row)

    def get(self, actual_penalty_id: UUID) -> dict | None:
        """Fetch one `actual_penalty` row by its own surrogate id.

        Mirrors `PenaltyProjectionRepository.get_by_id` -- backs
        `GET /penalties/actual-penalties/{actual_penalty_id}`; every other
        method here is keyed by `purchase_order_id`, not by this table's own
        `id`."""
        row = self._session.get(ActualPenalty, actual_penalty_id)
        return _actual_penalty_to_dict(row) if row is not None else None

    def list_actual_penalties(self, purchase_order_id: UUID | None = None) -> list[dict]:
        """General `actual_penalty` row filter backing
        `GET /penalties/actual-penalties?purchase_order_id=` -- mirrors
        `PenaltyProjectionRepository.list_projections`'s own optional
        `purchase_order_id` filter: given, every row for that PO (same shape
        `list_for_purchase_order` below already returned); omitted, every
        row across every PO."""
        query = select(ActualPenalty)
        if purchase_order_id is not None:
            query = query.where(ActualPenalty.purchase_order_id == purchase_order_id)
        rows = self._session.scalars(query).all()
        return [_actual_penalty_to_dict(r) for r in rows]

    def list_for_purchase_order(self, purchase_order_id: UUID) -> list[dict]:
        """Full, unfiltered actual-penalty list for one PO -- kept as its
        own narrow method for existing callers (`ProjectionSummaryService`)
        that only ever need this shape, same reason
        `PenaltyProjectionRepository.list_history` still wraps
        `list_projections` above."""
        return self.list_actual_penalties(purchase_order_id=purchase_order_id)

    def truncate_all(self) -> None:
        """Deletes every actual_penalty row, for a force-reseed. FKs to
        purchase_order, so must run before
        `PurchaseOrderRepository.truncate_all()` clears it. Deferred by
        Phase 2 to this phase -- see the sibling
        `PenaltyProjectionRepository.truncate_all()`'s docstring."""
        self._session.execute(delete(ActualPenalty))
        self._session.flush()
