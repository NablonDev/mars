"""Retailer master data."""

from uuid import UUID

from sqlalchemy import Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, TimestampMixin, generate_uuid7


class Retailer(Base, TimestampMixin):
    __tablename__ = "retailer"

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    retailer_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    retailer_name: Mapped[str] = mapped_column(String(200))
    priority_tier: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # SUM or MAX; retailer-specific stacking policy.
    stacking_mode: Mapped[str] = mapped_column(String(30), default="SUM")
    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Per-retailer PO delivery-change-request policy. Not in
    # docs/redesigned-schema.md's common.retailer table (that doc predates
    # this feature) but real, currently-used business data backing
    # PoDeliveryChangeRequest -- kept rather than dropped.
    extension_min_lead_days: Mapped[int] = mapped_column(Integer, default=2)
    extension_response_sla_hours: Mapped[int] = mapped_column(Integer, default=48)
    extension_penalty_threshold: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
