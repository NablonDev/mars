"""Dimension tables: retailers, SKUs, locations, carriers."""

from uuid import UUID

from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, generate_uuid7


class Retailer(Base):
    __tablename__ = "dim_retailer"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    retailer_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    retailer_name: Mapped[str] = mapped_column(String(100))
    priority_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # SUM or MAX -- see docs/FINE_ENGINE.md "Stacking" for why this needs
    # confirming per retailer rather than assuming one answer fits both,
    # especially once two violations can share one root cause (Gap 2).
    stacking_mode: Mapped[str] = mapped_column(String(10), default="SUM")


class Sku(Base):
    __tablename__ = "dim_sku"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    sku_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    sku_code: Mapped[str] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Location(Base):
    __tablename__ = "dim_location"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    location_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    location_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_type: Mapped[str | None] = mapped_column(String(10), nullable=True)


class Carrier(Base):
    __tablename__ = "dim_carrier"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    carrier_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    carrier_name: Mapped[str] = mapped_column(String(100))
    historical_reliability_score: Mapped[float] = mapped_column(Numeric(5, 2), default=90.0)
