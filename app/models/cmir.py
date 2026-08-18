from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, Base


class CMIRRecordORM(Base):
    """Approved CMIR record."""

    __tablename__ = "cmir_records"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    sender_type: Mapped[str] = mapped_column(Text)
    customer_identity: Mapped[str] = mapped_column(Text)
    material_identity: Mapped[str] = mapped_column(Text)
    intent_phrase: Mapped[str] = mapped_column(Text)
    existing_cmir_ref: Mapped[str] = mapped_column(Text)
    brand: Mapped[str] = mapped_column(Text)
    site: Mapped[str] = mapped_column(Text)
    target_grd_code: Mapped[str] = mapped_column(Text)
    target_customer_material_ref: Mapped[str] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    # Format-insensitive matching keys (app.services.identity.normalize_identity_key)
    # computed at write time by PostgresCMIRRepository. Raw columns above are stored/
    # displayed exactly as received; these two drive lookup and the uniqueness index
    # instead, so "Cust-9900"/"cust9900"/"CUST-9900" all resolve to the same entity.
    customer_identity_key: Mapped[str] = mapped_column(Text)
    target_customer_material_ref_key: Mapped[str] = mapped_column(Text)
    # SCD2 versioning (migrations/schema.sql). valid_from was the pre-existing,
    # previously-unmapped approved_at column, renamed rather than duplicated --
    # see docs/memory.md design decision #9.
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{CMIR_SCHEMA}.cmir_records.id")
    )
