"""Production order and its schedule -- historized status facts."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UUID_PK, Base, TimestampMixin, generate_uuid7


class ProductionOrder(Base, TimestampMixin):
    __tablename__ = "production_order"

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    production_order_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    material_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("material.id"), nullable=True)
    plant_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("plant.id"), nullable=True)
    planned_quantity: Mapped[float | None] = mapped_column(Numeric(18, 3), nullable=True)
    produced_quantity: Mapped[float | None] = mapped_column(Numeric(18, 3), nullable=True)
    planned_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="PLANNED")


class ProductionSchedule(Base, TimestampMixin):
    """Historized production status; keyed by (material_id, plant_id), not
    a single order -- a production line serves whichever orders draw on it.
    See tests/unit/services/test_known_limitations.py for the one place in
    the mock data where two orders sharing a line is a real, accepted
    wrinkle rather than a bug."""

    __tablename__ = "production_schedule"

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    production_order_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("production_order.id"), nullable=True
    )
    material_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("material.id"), nullable=True)
    plant_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("plant.id"))
    scheduled_quantity: Mapped[float | None] = mapped_column(Numeric(18, 3), nullable=True)
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50))  # ON_TRACK / AT_RISK / BEHIND
    status_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
