"""Database models for projected fines and post-delivery actual fines."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import FINES_SCHEMA, UUID_PK, Base, generate_uuid7


class ProjectedFine(Base):
    """Periodic fine projection for an order, rule, and projection date."""

    __tablename__ = "projected_fine"
    __table_args__ = (
        UniqueConstraint("order_id", "rule_id", "projection_date", name="uq_projected_fine_order_rule_date"),
        {"schema": FINES_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    rule_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.fine_rule.rule_id"))
    projection_date: Mapped[date] = mapped_column(Date)
    violation_type: Mapped[str] = mapped_column(String(30))
    failure_probability: Mapped[float] = mapped_column(Numeric(5, 4))
    projected_fine_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    days_to_delivery: Mapped[int] = mapped_column(Integer)
    projection_status: Mapped[str] = mapped_column(String(30), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActualFine(Base):
    """Post-delivery fine recorded against an order."""

    __tablename__ = "actual_fine"
    __table_args__ = ({"schema": FINES_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    actual_fine_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.sales_order.order_id"))
    retailer_id: Mapped[str] = mapped_column(ForeignKey(f"{FINES_SCHEMA}.retailer.retailer_id"))
    violation_type: Mapped[str] = mapped_column(String(30))
    actual_fine_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    invoice_or_deduction_date: Mapped[date] = mapped_column(Date)
    dispute_status: Mapped[str] = mapped_column(String(30), default="NONE")  # NONE/DISPUTED/WAIVED/UPHELD
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
