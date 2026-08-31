"""Carrier master data."""

from uuid import UUID

from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import COMMON_SCHEMA, UUID_PK, Base, TimestampMixin, generate_uuid7


class Carrier(Base, TimestampMixin):
    __tablename__ = "carrier"
    __table_args__ = ({"schema": COMMON_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    carrier_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    carrier_name: Mapped[str] = mapped_column(String(200))
    historical_reliability_score: Mapped[float] = mapped_column(Numeric(5, 2), default=90.0)
