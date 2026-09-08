"""SKU master data."""

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, TimestampMixin, generate_uuid7


class Sku(Base, TimestampMixin):
    """Stock keeping unit (SKU) master data."""

    __tablename__ = "sku"

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    sku_code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("material.id"), nullable=True)
