from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CMIR_SCHEMA, JSONB_OR_JSON, UUID_PK, Base, generate_uuid7


class PoLineORM(Base):
    """One row per PO line under validation by the PO Validation Agent."""

    __tablename__ = "po_lines"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    batch_id: Mapped[str] = mapped_column(String(150))
    po_number: Mapped[str] = mapped_column(String(50))
    po_line_number: Mapped[str] = mapped_column(String(30))
    customer_id: Mapped[str] = mapped_column(String(50))
    customer_material_code: Mapped[str] = mapped_column(String(100))
    plant: Mapped[str] = mapped_column(String(30))
    order_quantity: Mapped[float] = mapped_column(Numeric)
    uom: Mapped[str | None] = mapped_column(String(30))
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB_OR_JSON)
    status: Mapped[str] = mapped_column(String(50), default="NEW")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaterialMasterORM(Base):
    """Local mirror of the SAP MARC fields this agent needs, keyed by (material, plant)."""

    __tablename__ = "material_master"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    sap_material_number: Mapped[str] = mapped_column(String(50))
    plant: Mapped[str] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(Text)
    available_quantity: Mapped[float] = mapped_column(Numeric, default=0)
    uom: Mapped[str | None] = mapped_column(String(30))
    discontinuation_indicator: Mapped[str | None] = mapped_column(String(30))
    effective_out_date: Mapped[date | None] = mapped_column(Date)
    follow_up_material_number: Mapped[str | None] = mapped_column(String(100))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PoLineErrorORM(Base):
    """One row per validation/processing failure on a PO line."""

    __tablename__ = "po_line_errors"
    __table_args__ = ({"schema": CMIR_SCHEMA},)

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=generate_uuid7)
    po_line_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.po_lines.id"))
    agent_run_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id"))
    error_type: Mapped[str] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    node_name: Mapped[str | None] = mapped_column(String(150))
    raw_error_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB_OR_JSON)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(150))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
