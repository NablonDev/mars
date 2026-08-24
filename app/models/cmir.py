from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class CMIRRecordORM(Base, TimestampMixin):
    """Approved CMIR record."""

    __tablename__ = "cmir_records"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    email_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_events.id"))
    sender_type: Mapped[str] = mapped_column(String(100))
    customer_identity: Mapped[str] = mapped_column(String(255))
    material_identity: Mapped[str] = mapped_column(String(255))
    intent_phrase: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_cmir_ref: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str] = mapped_column(String(100))
    site: Mapped[str] = mapped_column(String(100))
    target_grd_code: Mapped[str] = mapped_column(String(255))
    target_customer_material_ref: Mapped[str] = mapped_column(String(255))
    effective_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Format-insensitive matching keys (app.services.identity.normalize_identity_key)
    # computed at write time by PostgresCMIRRepository. Raw columns above are stored/
    # displayed exactly as received; these two drive lookup and the uniqueness index
    # instead, so "Cust-9900"/"cust9900"/"CUST-9900" all resolve to the same entity.
    customer_identity_key: Mapped[str] = mapped_column(String(255))
    target_customer_material_ref_key: Mapped[str] = mapped_column(String(255))
    # SCD2 versioning (migrations/schema.sql). valid_from was the pre-existing,
    # previously-unmapped approved_at column, renamed rather than duplicated --
    # see docs/memory.md design decision #9.
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.cmir_records.id")
    )
