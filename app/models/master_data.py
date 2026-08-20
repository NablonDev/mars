"""Dimension tables: retailers, SKUs, locations, and carriers."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7


class Retailer(Base):
    __tablename__ = "dim_retailer"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    retailer_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    retailer_name: Mapped[str] = mapped_column(String(100))
    priority_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # SUM or MAX; retailer-specific stacking policy.
    stacking_mode: Mapped[str] = mapped_column(String(10), default="SUM")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Sku(Base):
    __tablename__ = "dim_sku"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    sku_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    sku_code: Mapped[str] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Location(Base):
    __tablename__ = "dim_location"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    location_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    location_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Carrier(Base):
    __tablename__ = "dim_carrier"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    carrier_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    carrier_name: Mapped[str] = mapped_column(String(100))
    historical_reliability_score: Mapped[float] = mapped_column(Numeric(5, 2), default=90.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
