"""Repository for cmir.cmir_record, SCD2-versioned CMIR mapping history."""

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
    """Raised when the current record changed since the version being written was read."""


def _to_dict(row: CmirRecord) -> dict[str, Any]:
    """Project a `CmirRecord` row onto the content fields callers see, without SCD2 bookkeeping."""
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
    """SCD2-versioned CMIR mapping history.

    At most one `is_current` row exists per (customer_identity,
    target_customer_material_ref) pair; supersessions are recorded rather than
    overwritten in place.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_latest_for_customer_material(
        self, customer_identity: str, target_customer_material_ref: str
    ) -> dict[str, Any] | None:
        """Return the current row for this customer/material pair, or None.

        Kept separate from `get_current` because PO validation needs only the four
        fields returned here, not the full content-field set.
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

        Callers reach this only after `validate_against_cmir` found no current
        mapping, so "no current row" is a precondition. Routing through
        `supersede_and_insert` keeps the one-current-per-entity invariant, turning a
        concurrent insert into `CmirVersionConflict` instead of a second current row.
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
        """Retire the current row for this entity and insert `merged` as its replacement.

        `expected_current_id` must match the row that is actually current (`None`
        when none is expected yet); a mismatch raises `CmirVersionConflict` before
        anything is written. The old row is flushed to `is_current=False` first, so
        the partial unique index never sees two current rows for one entity; a
        concurrent writer that slips into that window hits the same index and also
        raises `CmirVersionConflict`. Returns the id of the new row.
        """
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
            # The partial unique index checks immediately rather than at commit, so
            # the old row must stop being current before the new one is inserted.
            # This flush forces the UPDATE ahead of the INSERT below, overriding
            # SQLAlchemy's default insert-before-update ordering.
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
            # The partial unique index is the real correctness guarantee; the id
            # comparison above is a best-effort early exit for a clean error message.
            # A writer that won the race in between is caught here instead.
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
