from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.cmir import SnapshotHistoryItem

# ---------------------------------------------------------------------------
# Value objects (formerly po_validation/domain/models.py).
# ---------------------------------------------------------------------------


class PoLineStatus(str, Enum):
    NEW = "NEW"
    VALIDATING = "VALIDATING"
    AWAITING_DECISION = "AWAITING_DECISION"
    READY_FOR_SO_CREATION = "READY_FOR_SO_CREATION"
    READY_FOR_SO_CREATION_PARTIAL = "READY_FOR_SO_CREATION_PARTIAL"
    DISCONTINUED = "DISCONTINUED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class PoLine:
    """One PO line submitted for CMIR/Material Master validation."""

    batch_id: str
    po_number: str
    po_line_number: str
    customer_id: str
    customer_material_code: str
    plant: str
    order_quantity: float
    raw_payload: dict[str, Any]
    id: str | None = None
    uom: str | None = None
    requested_delivery_date: str | None = None
    status: str = PoLineStatus.NEW.value


@dataclass(frozen=True)
class MaterialMasterRecord:
    """Local mirror of one SAP MARC row, keyed by (sap_material_number, plant)."""

    sap_material_number: str
    plant: str
    available_quantity: float
    description: str | None = None
    uom: str | None = None
    discontinuation_indicator: str | None = None
    effective_out_date: str | None = None
    follow_up_material_number: str | None = None


@dataclass(frozen=True)
class PoLineError:
    """One validation/processing failure recorded against a PO line."""

    po_line_id: str
    error_type: str
    node_name: str
    agent_run_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw_error_detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# API request/response DTOs (formerly po_validation/schemas.py).
# ---------------------------------------------------------------------------


class IngestPoLineItem(BaseModel):
    po_number: str
    po_line_number: str
    customer_id: str
    customer_material_code: str
    plant: str
    order_quantity: float
    uom: str | None = None
    requested_delivery_date: str | None = None


class IngestPoLinesRequest(BaseModel):
    lines: list[IngestPoLineItem] = Field(min_length=1)


class PoLineSummary(BaseModel):
    po_line_id: str
    batch_id: str
    po_number: str
    po_line_number: str
    status: str
    thread_id: str | None = None
    updated_at: str | None = None


class IngestPoLinesResponse(BaseModel):
    batch_id: str
    total_lines: int
    lines: list[PoLineSummary]


class PoLinesListResponse(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None


class PoLineErrorItem(BaseModel):
    id: str
    po_line_id: str
    agent_run_id: UUID | None = None
    error_type: str
    error_code: str | None = None
    error_message: str | None = None
    node_name: str | None = None
    occurred_at: str | None = None
    resolved: bool = False


class PoLineErrorsResponse(BaseModel):
    items: list[PoLineErrorItem]


class QtyMismatchDecisionRequest(BaseModel):
    actor: str
    decision: Literal["use_substitute", "proceed_anyway", "mark_stale"]
    substitute_material_code: str | None = None
    expected_updated_at: str


class ManualCmirEntryRequest(BaseModel):
    actor: str
    sap_material_number: str
    description: str = ""
    expected_updated_at: str


class CandidateInfo(BaseModel):
    sap_material_number: str
    plant: str
    available_quantity: float
    shortfall: float
    suggested_substitute_material_code: str | None = None


class PoThreadStageResponse(BaseModel):
    batch_id: str | None = None
    agent_run_id: UUID
    thread_id: str
    po_line_id: str
    stage: str
    status: str
    current_node: str | None = None
    pending_action_id: int | None = None
    updated_at: str | None = None


class PoThreadSnapshotResponse(BaseModel):
    agent_run_id: UUID
    thread_id: str
    po_line_id: str
    po_number: str
    po_line_number: str
    customer_material_code: str
    order_quantity: float
    stage: str
    candidate: CandidateInfo | None = None
    editable_fields: list[str]
    history: list[SnapshotHistoryItem]
    updated_at: str | None = None
