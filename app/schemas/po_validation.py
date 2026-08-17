from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

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
    raw_payload: Dict[str, Any]
    id: Optional[str] = None
    uom: Optional[str] = None
    requested_delivery_date: Optional[str] = None
    status: str = PoLineStatus.NEW.value


@dataclass(frozen=True)
class MaterialMasterRecord:
    """Local mirror of one SAP MARC row, keyed by (sap_material_number, plant)."""

    sap_material_number: str
    plant: str
    available_quantity: float
    description: Optional[str] = None
    uom: Optional[str] = None
    discontinuation_indicator: Optional[str] = None
    effective_out_date: Optional[str] = None
    follow_up_material_number: Optional[str] = None


@dataclass(frozen=True)
class PoLineError:
    """One validation/processing failure recorded against a PO line."""

    po_line_id: str
    error_type: str
    node_name: str
    agent_run_id: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_error_detail: Dict[str, Any] = field(default_factory=dict)


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
    uom: Optional[str] = None
    requested_delivery_date: Optional[str] = None


class IngestPoLinesRequest(BaseModel):
    lines: List[IngestPoLineItem] = Field(min_length=1)


class PoLineSummary(BaseModel):
    po_line_id: str
    batch_id: str
    po_number: str
    po_line_number: str
    status: str
    thread_id: Optional[str] = None
    updated_at: Optional[str] = None


class IngestPoLinesResponse(BaseModel):
    batch_id: str
    total_lines: int
    lines: List[PoLineSummary]


class PoLinesListResponse(BaseModel):
    items: List[Dict[str, Any]]
    next_cursor: Optional[str] = None


class PoLineErrorItem(BaseModel):
    id: str
    po_line_id: str
    agent_run_id: Optional[int] = None
    error_type: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    node_name: Optional[str] = None
    occurred_at: Optional[str] = None
    resolved: bool = False


class PoLineErrorsResponse(BaseModel):
    items: List[PoLineErrorItem]


class QtyMismatchDecisionRequest(BaseModel):
    actor: str
    decision: Literal["use_substitute", "proceed_anyway", "mark_stale"]
    substitute_material_code: Optional[str] = None
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
    suggested_substitute_material_code: Optional[str] = None


class PoThreadStageResponse(BaseModel):
    batch_id: Optional[str] = None
    agent_run_id: int
    thread_id: str
    po_line_id: str
    stage: str
    status: str
    current_node: Optional[str] = None
    pending_action_id: Optional[int] = None
    updated_at: Optional[str] = None


class PoThreadSnapshotResponse(BaseModel):
    agent_run_id: int
    thread_id: str
    po_line_id: str
    po_number: str
    po_line_number: str
    customer_material_code: str
    order_quantity: float
    stage: str
    candidate: Optional[CandidateInfo] = None
    editable_fields: List[str]
    history: List[SnapshotHistoryItem]
    updated_at: Optional[str] = None
