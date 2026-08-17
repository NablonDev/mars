from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import Database
from app.models.cmir import CMIRRecordORM
from app.schemas.cmir import CMIR
from app.services.identity import normalize_identity_key

logger = logging.getLogger(__name__)


class CMIRVersionConflict(Exception):
    """Raised by PostgresCMIRRepository.supersede_and_insert when the current record for an
    entity changed since the version this write was based on was read.

    Can come from either the cheap app-level id comparison (the common case) or from
    the database rejecting the insert against the partial unique index on
    (customer_identity, target_customer_material_ref) WHERE is_current -- the index is
    the actual correctness guarantee; the id comparison is just an early, cheaper exit
    for a clean error message. Callers must treat both sources identically.
    """


class PostgresCMIRRepository:
    """SQLAlchemy repository for approved CMIR records."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def find_latest_for_customer_material(
        self, customer_identity: str, target_customer_material_ref: str
    ) -> Optional[Dict[str, Any]]:
        """Return the current (is_current) cmir_records row for this customer/material pair.

        Used by PO Validation. Shares the same is_current invariant as get_current
        below (added for the CMIR agent's SCD2 versioning) -- kept as its own method
        rather than merged with get_current because it returns a narrower dict shape
        (the four fields PO Validation actually uses), not the full
        CMIR_CONTENT_FIELDS set.
        """
        with self._db.session() as session:
            row = session.scalar(
                select(CMIRRecordORM).where(
                    CMIRRecordORM.customer_identity_key == normalize_identity_key(customer_identity),
                    CMIRRecordORM.target_customer_material_ref_key
                    == normalize_identity_key(target_customer_material_ref),
                    CMIRRecordORM.is_current.is_(True),
                )
            )
        if row is None:
            return None
        return {
            "id": row.id,
            "customer_identity": row.customer_identity,
            "material_identity": row.material_identity,
            "target_customer_material_ref": row.target_customer_material_ref,
        }

    def create_manual_mapping(
        self,
        *,
        customer_identity: str,
        material_identity: str,
        target_customer_material_ref: str,
        description: str = "",
    ) -> int:
        """Insert a CMIR mapping from a human-submitted PO line entry.

        Reuses supersede_and_insert (with expected_current_id=None) instead of a bare
        insert: this method is only ever called after validate_against_cmir found no
        current mapping, so "still none now" is the precondition, not just the common
        case. Reusing the same path means the one-current-per-entity invariant holds
        here too, and a rare race (something else created a current mapping in the
        meantime) surfaces as CMIRVersionConflict instead of silently leaving two
        rows both claiming to be current for the same entity. PO Validation's
        create_cmir_record node already wraps this call in error-capturing that turns
        any exception into a po_line_errors row, so no caller-side change is needed.
        """
        merged = CMIR(
            customer_identity=customer_identity,
            material_identity=material_identity,
            target_customer_material_ref=target_customer_material_ref,
            reason=description or "",
        )
        return self.supersede_and_insert(
            customer_identity=customer_identity,
            target_customer_material_ref=target_customer_material_ref,
            merged=merged,
            expected_current_id=None,
        )

    def get_current(
        self, customer_identity: str, target_customer_material_ref: str
    ) -> Optional[Dict[str, Any]]:
        """Return the current (is_current) cmir_records row for this entity, or None.

        The returned dict carries `id` plus every field in
        app.schemas.cmir.CMIR_CONTENT_FIELDS -- the shape app.services.cmir_merge's
        merge_with_active expects for its `existing` argument.
        """
        with self._db.session() as session:
            row = session.scalar(
                select(CMIRRecordORM).where(
                    CMIRRecordORM.customer_identity_key == normalize_identity_key(customer_identity),
                    CMIRRecordORM.target_customer_material_ref_key
                    == normalize_identity_key(target_customer_material_ref),
                    CMIRRecordORM.is_current.is_(True),
                )
            )
        if row is None:
            return None
        return {
            "id": row.id,
            "sender_type": row.sender_type,
            "customer_identity": row.customer_identity,
            "material_identity": row.material_identity,
            "intent_phrase": row.intent_phrase,
            "existing_cmir_ref": row.existing_cmir_ref,
            "brand": row.brand,
            "site": row.site,
            "target_grd_code": row.target_grd_code,
            "target_customer_material_ref": row.target_customer_material_ref,
            "effective_date": row.effective_date.isoformat() if row.effective_date else "",
            "reason": row.reason,
        }

    def supersede_and_insert(
        self,
        *,
        customer_identity: str,
        target_customer_material_ref: str,
        merged: CMIR,
        expected_current_id: Optional[int],
    ) -> int:
        customer_identity_key = normalize_identity_key(customer_identity)
        target_customer_material_ref_key = normalize_identity_key(target_customer_material_ref)
        with self._db.session() as session:
            current = session.scalar(
                select(CMIRRecordORM).where(
                    CMIRRecordORM.customer_identity_key == customer_identity_key,
                    CMIRRecordORM.target_customer_material_ref_key == target_customer_material_ref_key,
                    CMIRRecordORM.is_current.is_(True),
                )
            )
            current_id = current.id if current is not None else None
            if current_id != expected_current_id:
                raise CMIRVersionConflict(
                    f"Current record for customer_identity={customer_identity!r}, "
                    f"target_customer_material_ref={target_customer_material_ref!r} changed: "
                    f"expected current id={expected_current_id}, found id={current_id}."
                )

            if current is not None:
                # Free the one-current-per-entity slot before inserting the
                # replacement -- the partial unique index checks immediately, not at
                # commit, so the old row must stop being current before the new one
                # can become current. An explicit flush here forces this UPDATE to
                # reach the database before the INSERT below, overriding SQLAlchemy's
                # default insert-before-update flush ordering.
                current.is_current = False
                current.valid_to = func.now()
                session.flush()

            new_record = CMIRRecordORM(
                email_id=None,
                sender_type=merged.sender_type,
                customer_identity=merged.customer_identity,
                customer_identity_key=normalize_identity_key(merged.customer_identity),
                material_identity=merged.material_identity,
                intent_phrase=merged.intent_phrase,
                existing_cmir_ref=merged.existing_cmir_ref,
                brand=merged.brand,
                site=merged.site,
                target_grd_code=merged.target_grd_code,
                target_customer_material_ref=merged.target_customer_material_ref,
                target_customer_material_ref_key=normalize_identity_key(
                    merged.target_customer_material_ref
                ),
                effective_date=merged.effective_date or None,
                reason=merged.reason,
                is_current=True,
            )
            session.add(new_record)
            try:
                session.flush()
            except IntegrityError as exc:
                # The partial unique index is the true correctness guarantee -- the
                # id comparison above is a best-effort early exit for a clean error
                # message. If a concurrent writer won the race in the gap between
                # that check and this flush, the constraint catches it here instead.
                raise CMIRVersionConflict(
                    f"Concurrent write detected for customer_identity={customer_identity!r}, "
                    f"target_customer_material_ref={target_customer_material_ref!r} while "
                    f"inserting the new version."
                ) from exc

            if current is not None:
                current.superseded_by_id = new_record.id

            new_id = new_record.id

        logger.info(
            "Superseded cmir_records id=%s with new current id=%s for customer=%s material_ref=%s",
            current_id,
            new_id,
            customer_identity,
            target_customer_material_ref,
        )
        return new_id
