"""Plant-agnostic material identity and per-plant material/stock detail.

`material` is referenced by `sku`, `material_master`, `purchase_order_line`,
`production_order`, and `production_schedule`. `material_master` is one row
per (material, plant), not one row per material -- the SAP MARA/MARC split.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import COMMON_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class Material(Base, TimestampMixin):
    __tablename__ = "material"
    __table_args__ = ({"schema": COMMON_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    material_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class MaterialMaster(Base, TimestampMixin):
    """Per-plant stock/logistics detail for one material."""

    __tablename__ = "material_master"
    __table_args__ = (
        UniqueConstraint("material_id", "plant_id", name="uq_material_master_material_plant"),
        {"schema": COMMON_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    material_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{COMMON_SCHEMA}.material.id"))
    sap_material_number: Mapped[str] = mapped_column(String(64))
    plant_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{COMMON_SCHEMA}.plant.id"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_quantity: Mapped[float | None] = mapped_column(Numeric(18, 3), nullable=True)
    uom: Mapped[str | None] = mapped_column(String(30), nullable=True)
    discontinuation_indicator: Mapped[str | None] = mapped_column(String(30), nullable=True)
    effective_out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    follow_up_material_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{COMMON_SCHEMA}.material.id"), nullable=True
    )
    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
