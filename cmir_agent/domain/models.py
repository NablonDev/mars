from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple

from pydantic import BaseModel, Field


class CMIRStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    PENDING_HUMAN_ACTION = "pending_human_action"
    APPROVED = "approved"
    REJECTED = "rejected"


MANDATORY_FIELDS: Tuple[str, ...] = (
    "sender_type",
    "customer_identity",
    "material_identity",
    "intent_phrase",
    "existing_cmir_ref",
    "brand",
    "site",
)


class CMIR(BaseModel):
    sender_type: str = ""
    customer_identity: str = ""
    material_identity: str = ""
    intent_phrase: str = ""
    existing_cmir_ref: str = ""
    brand: str = ""
    site: str = ""
    target_grd_code: str = ""
    target_customer_material_ref: str = ""
    effective_date: str = ""
    reason: str = ""
    status: str = ""
    missing_fields: List[str] = Field(default_factory=list)


@dataclass(frozen=True)
class EmailMessage:
    """Normalized representation of an inbound CMIR email."""
    imap_id: str
    sender: str
    subject: str
    body: str
