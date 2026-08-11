"""Fine rules and their optional tiered bands."""

from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, generate_uuid7


class FineRuleORM(Base):
    __tablename__ = "dim_fine_rule"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    rule_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    retailer_id: Mapped[str] = mapped_column(ForeignKey("dim_retailer.retailer_id"))
    violation_type: Mapped[str] = mapped_column(String(30))
    # FRACTION, e.g. 0.02 for 2% -- never a whole-number percent. See
    # docs/FINE_ENGINE.md changelog: this file and the schema doc both
    # used to disagree on the convention, which is exactly the kind of
    # thing that silently prices a rule 100x too aggressively.
    threshold_pct: Mapped[float] = mapped_column(Numeric(6, 4), default=0.0)
    calc_type: Mapped[str] = mapped_column(String(20))  # PER_UNIT / PERCENT_OF_PO / FLAT_FEE / TIERED
    rate: Mapped[float] = mapped_column(Numeric(10, 4), default=0.0)
    cap_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    grace_period_days: Mapped[int] = mapped_column(Integer, default=0)
    effective_start_date: Mapped[date] = mapped_column(Date, default=date(2026, 1, 1))
    effective_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_doc_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)


class FineRuleTier(Base):
    """One band of a TIERED rule. See app.engine.FineTier -- this table
    is the DB-backed source that gets loaded into that dataclass by
    repositories/fine_rule_repository.py."""

    __tablename__ = "dim_fine_rule_tier"
    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    tier_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("dim_fine_rule.rule_id"))
    band_min: Mapped[float] = mapped_column(Numeric(6, 4))
    band_max: Mapped[float] = mapped_column(Numeric(6, 4))
    rate: Mapped[float] = mapped_column(Numeric(10, 4))
