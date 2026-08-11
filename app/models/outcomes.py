"""Projection output and post-delivery actuals -- the two tables the
'Projected Fines' and 'Actual Fines' KPIs read from."""

from datetime import date
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, generate_uuid7


class ProjectedFine(Base):
    """One row per order/rule/day -- a periodic snapshot fact, not a
    current-state row. `id` is the real primary key; (order_id, rule_id,
    projection_date) is a unique constraint, which is what
    ProjectionRepository.save_result upserts against."""

    __tablename__ = "fact_projected_fine"
    __table_args__ = (
        UniqueConstraint("order_id", "rule_id", "projection_date", name="uq_projected_fine_order_rule_date"),
    )
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey("fact_order.order_id"))
    rule_id: Mapped[str] = mapped_column(ForeignKey("dim_fine_rule.rule_id"))
    projection_date: Mapped[date] = mapped_column(Date)
    violation_type: Mapped[str] = mapped_column(String(30))
    failure_probability: Mapped[float] = mapped_column(Numeric(5, 4))
    projected_fine_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    days_to_delivery: Mapped[int] = mapped_column(Integer)
    projection_status: Mapped[str] = mapped_column(String(20), default="OPEN")


class ActualFine(Base):
    __tablename__ = "fact_actual_fine"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    actual_fine_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("fact_order.order_id"))
    retailer_id: Mapped[str] = mapped_column(ForeignKey("dim_retailer.retailer_id"))
    violation_type: Mapped[str] = mapped_column(String(30))
    actual_fine_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    invoice_or_deduction_date: Mapped[date] = mapped_column(Date)
    dispute_status: Mapped[str] = mapped_column(String(20), default="NONE")  # NONE/DISPUTED/WAIVED/UPHELD
