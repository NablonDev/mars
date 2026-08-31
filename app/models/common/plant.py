"""Plant, storage location, and warehouse master data."""

from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import COMMON_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class Plant(Base, TimestampMixin):
    __tablename__ = "plant"
    __table_args__ = ({"schema": COMMON_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    plant_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    plant_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(10), nullable=True)


class StorageLocation(Base, TimestampMixin):
    __tablename__ = "storage_location"
    __table_args__ = (
        UniqueConstraint("plant_id", "storage_location_code", name="uq_storage_location_plant_code"),
        {"schema": COMMON_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    plant_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{COMMON_SCHEMA}.plant.id"))
    storage_location_code: Mapped[str] = mapped_column(String(50))
    storage_location_name: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Warehouse(Base, TimestampMixin):
    __tablename__ = "warehouse"
    __table_args__ = ({"schema": COMMON_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    warehouse_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    warehouse_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    plant_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey(f"{COMMON_SCHEMA}.plant.id"), nullable=True
    )
