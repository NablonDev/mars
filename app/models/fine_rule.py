"""Fine rules and their optional tiered bands."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7


class FineRule(Base):
    __tablename__ = "fine_rule"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    rule_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    retailer_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.retailer.retailer_id"))
    violation_type: Mapped[str] = mapped_column(String(30))
    threshold_pct: Mapped[float] = mapped_column(Numeric(6, 4), default=0.0)
    calc_type: Mapped[str] = mapped_column(String(30))  # PER_UNIT / PERCENT_OF_PO / FLAT_FEE / TIERED
    rate: Mapped[float] = mapped_column(Numeric(10, 4), default=0.0)
    cap_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    grace_period_days: Mapped[int] = mapped_column(Integer, default=0)
    effective_start_date: Mapped[date] = mapped_column(Date, default=date(2026, 1, 1))
    effective_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_doc_reference: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FineRuleTier(Base):
    """One tier band belonging to a tiered fine rule."""

    __tablename__ = "fine_rule_tier"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    tier_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.fine_rule.rule_id"))
    band_min: Mapped[float] = mapped_column(Numeric(6, 4))
    band_max: Mapped[float] = mapped_column(Numeric(6, 4))
    rate: Mapped[float] = mapped_column(Numeric(10, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
