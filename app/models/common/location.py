"""Retailer-owned ship-to locations."""

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, TimestampMixin, generate_uuid7


class RetailerLocation(Base, TimestampMixin):
    __tablename__ = "retailer_location"
    __table_args__ = (
        UniqueConstraint("retailer_id", "location_code", name="uq_retailer_location_retailer_code"),
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    retailer_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("retailer.id"))
    location_code: Mapped[str] = mapped_column(String(100))
    location_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address_line_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state_province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
