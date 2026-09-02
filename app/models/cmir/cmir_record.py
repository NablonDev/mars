"""SCD2 history of approved CMIR mappings -- not an append-only log.
Exactly one `is_current = true` row per `(customer_identity_key,
target_customer_material_ref_key)`, enforced by a partial unique index
declared as raw migration DDL only (see the `cmir` schema revision) --
not expressible as a portable ORM `Index(postgresql_where=...)` without
silently becoming a full unique index on SQLite."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class CmirRecord(Base, TimestampMixin):
    __tablename__ = "cmir_record"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    purchase_order_line_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("purchase_order_line.id"), nullable=True
    )
    email_event_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.email_event.id"), nullable=True
    )
    sender_type: Mapped[str] = mapped_column(String(100))
    customer_identity: Mapped[str] = mapped_column(String(255))
    material_identity: Mapped[str] = mapped_column(String(255))
    intent_phrase: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_cmir_ref: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str] = mapped_column(String(100))
    site: Mapped[str] = mapped_column(String(100))
    target_grd_code: Mapped[str] = mapped_column(String(255))
    target_customer_material_ref: Mapped[str] = mapped_column(String(255))
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Format-insensitive matching keys (app.services.identity.normalize_identity_key)
    # computed at write time. Raw columns above are stored/displayed
    # exactly as received; these two drive lookup and the uniqueness index
    # instead, so "Cust-9900"/"cust9900"/"CUST-9900" all resolve to the
    # same entity.
    customer_identity_key: Mapped[str] = mapped_column(String(255))
    target_customer_material_ref_key: Mapped[str] = mapped_column(String(255))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.cmir_record.id"), nullable=True
    )
