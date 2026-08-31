"""Repository for `cmir.cmir_record` -- the SCD2-versioned CMIR mapping
history. Was `app/repositories/cmir.py` (`PostgresCMIRRepository`,
`CMIRRecordORM`); `app.models.cmir.cmir_record.CmirRecord` keeps the same
SCD2 shape (`is_current`/`valid_from`/`valid_to`/`superseded_by_id`) and the
same partial-unique-index invariant (one current row per
`(customer_identity_key, target_customer_material_ref_key)`, enforced by
raw migration DDL, not an ORM-level `Index`), so `supersede_and_insert`'s
logic is preserved as-is. Column rename: `email_id` -> `email_event_id`.

Switched, like the sibling `cmir` repositories, to the project's standard
injected-`Session` pattern instead of the old per-call `Database` session.
No dependence on `app.schemas.cmir.Cmir` (out of scope this phase):
callers pass the merged field values directly.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CmirRecord
from app.services.identity import normalize_identity_key

logger = logging.getLogger(__name__)


class CmirVersionConflict(Exception):
    """Raised by CmirRecordRepository.supersede_and_insert when the current record
    for an entity changed since the version this write was based on was read.

    Can come from either the cheap app-level id comparison (the common case) or from
    the database rejecting the insert against the partial unique index on
    (customer_identity_key, target_customer_material_ref_key) WHERE is_current -- the
    index is the actual correctness guarantee; the id comparison is just an early,
    cheaper exit for a clean error message. Callers must treat both sources identically.
    """


def _to_dict(row: CmirRecord) -> dict[str, Any]:
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


class CmirRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_latest_for_customer_material(
        self, customer_identity: str, target_customer_material_ref: str
    ) -> dict[str, Any] | None:
        """Return the current (is_current) cmir_record row for this customer/material pair.

        Used by PO Validation. Shares the same is_current invariant as get_current
        below -- kept as its own method rather than merged with get_current because it
        returns a narrower dict shape (the four fields PO Validation actually uses),
        not the full content-field set.
        """
        row = self._session.scalars(
            select(CmirRecord).where(
                CmirRecord.customer_identity_key == normalize_identity_key(customer_identity),
                CmirRecord.target_customer_material_ref_key
                == normalize_identity_key(target_customer_material_ref),
                CmirRecord.is_current.is_(True),
            )
        ).first()
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
    ) -> UUID:
        """Insert a CMIR mapping from a human-submitted PO line entry.

        Reuses supersede_and_insert (with expected_current_id=None) instead of a bare
        insert: this method is only ever called after validate_against_cmir found no
        current mapping, so "still none now" is the precondition, not just the common
        case. Reusing the same path means the one-current-per-entity invariant holds
        here too, and a rare race (something else created a current mapping in the
        meantime) surfaces as CmirVersionConflict instead of silently leaving two
        rows both claiming to be current for the same entity.
        """
        return self.supersede_and_insert(
            customer_identity=customer_identity,
            target_customer_material_ref=target_customer_material_ref,
            merged={
                "sender_type": "",
                "customer_identity": customer_identity,
                "material_identity": material_identity,
                "intent_phrase": None,
                "existing_cmir_ref": "",
                "brand": "",
                "site": "",
                "target_grd_code": "",
                "target_customer_material_ref": target_customer_material_ref,
                "effective_date": None,
                "reason": description or "",
            },
            expected_current_id=None,
        )

    def get_current(self, customer_identity: str, target_customer_material_ref: str) -> dict[str, Any] | None:
        """Return the current (is_current) cmir_record row for this entity, or None."""
        row = self._session.scalars(
            select(CmirRecord).where(
                CmirRecord.customer_identity_key == normalize_identity_key(customer_identity),
                CmirRecord.target_customer_material_ref_key
                == normalize_identity_key(target_customer_material_ref),
                CmirRecord.is_current.is_(True),
            )
        ).first()
        return _to_dict(row) if row is not None else None

    def supersede_and_insert(
        self,
        *,
        customer_identity: str,
        target_customer_material_ref: str,
        merged: dict[str, Any],
        expected_current_id: UUID | None,
    ) -> UUID:
        customer_identity_key = normalize_identity_key(customer_identity)
        target_customer_material_ref_key = normalize_identity_key(target_customer_material_ref)

        current = self._session.scalars(
            select(CmirRecord).where(
                CmirRecord.customer_identity_key == customer_identity_key,
                CmirRecord.target_customer_material_ref_key == target_customer_material_ref_key,
                CmirRecord.is_current.is_(True),
            )
        ).first()
        current_id = current.id if current is not None else None
        if current_id != expected_current_id:
            raise CmirVersionConflict(
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
            self._session.flush()

        new_record = CmirRecord(
            email_event_id=None,
            sender_type=merged["sender_type"],
            customer_identity=merged["customer_identity"],
            customer_identity_key=normalize_identity_key(merged["customer_identity"]),
            material_identity=merged["material_identity"],
            intent_phrase=merged.get("intent_phrase"),
            existing_cmir_ref=merged["existing_cmir_ref"],
            brand=merged["brand"],
            site=merged["site"],
            target_grd_code=merged["target_grd_code"],
            target_customer_material_ref=merged["target_customer_material_ref"],
            target_customer_material_ref_key=normalize_identity_key(merged["target_customer_material_ref"]),
            effective_date=merged.get("effective_date") or None,
            reason=merged.get("reason"),
            is_current=True,
        )
        self._session.add(new_record)
        try:
            self._session.flush()
        except IntegrityError as exc:
            # The partial unique index is the true correctness guarantee -- the
            # id comparison above is a best-effort early exit for a clean error
            # message. If a concurrent writer won the race in the gap between
            # that check and this flush, the constraint catches it here instead.
            self._session.rollback()
            raise CmirVersionConflict(
                f"Concurrent write detected for customer_identity={customer_identity!r}, "
                f"target_customer_material_ref={target_customer_material_ref!r} while "
                f"inserting the new version."
            ) from exc

        if current is not None:
            current.superseded_by_id = new_record.id
            self._session.flush()

        new_id = new_record.id
        logger.info(
            "Superseded cmir_record id=%s with new current id=%s for customer=%s material_ref=%s",
            current_id,
            new_id,
            customer_identity,
            target_customer_material_ref,
        )
        return new_id
