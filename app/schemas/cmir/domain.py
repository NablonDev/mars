"""Internal graph-state / value-object shapes for the CMIR domain (formerly
the top half of the flat `app/schemas/cmir.py`).

Framework-free except `Cmir` itself, which is a Pydantic model used as the
in-memory/graph-state CMIR representation, not an API contract -- it flows
through LangGraph state and gets reconstructed via `Cmir(**dict)` throughout
`app/agents/cmir/nodes.py` and `app/services/cmir/run_service.py`. Kept
verbatim from the pre-Phase-7b module: `app/agents/cmir/nodes.py`,
`app/services/cmir/{validation,merge,extractor,run_service}.py`, and
`app/services/email_reader.py` all import from here (via
`app/schemas/cmir/__init__.py`'s re-export) and are out of this phase's
scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field


class CmirStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    PENDING_HUMAN_ACTION = "pending_human_action"
    APPROVED = "approved"
    REJECTED = "rejected"


MANDATORY_FIELDS: tuple[str, ...] = (
    "sender_type",
    "customer_identity",
    "material_identity",
    "intent_phrase",
    "existing_cmir_ref",
    "brand",
    "site",
    "target_customer_material_ref",
)

# Every CMIR business-content field -- i.e. every field a reviewer can edit and every
# field the SCD2 merge (app.services.cmir.merge) considers. Deliberately excludes
# `status` and `missing_fields`, which are derived by CmirValidator from this
# content, not part of it.
CMIR_CONTENT_FIELDS: tuple[str, ...] = MANDATORY_FIELDS + (
    "target_grd_code",
    "effective_date",
    "reason",
)


class Cmir(BaseModel):
    sender_type: str = ""
    customer_identity: str = ""
    material_identity: str = Field(
        "",
        description=(
            "Internal/generic material identity mentioned in the email, not necessarily "
            "the customer's own material code."
        ),
    )
    intent_phrase: str = ""
    existing_cmir_ref: str = ""
    brand: str = ""
    site: str = ""
    target_grd_code: str = ""
    target_customer_material_ref: str = Field(
        "",
        description=(
            "The customer's own material number/reference as written in their email -- "
            "e.g. a line labeled 'Customer Material', 'Cust Mat No', 'Customer Material "
            "Code', or similar."
        ),
    )
    effective_date: str = ""
    reason: str = ""
    status: str = ""
    missing_fields: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class EmailMessage:
    """Normalized representation of an inbound CMIR email."""

    imap_id: str
    sender: str
    subject: str
    body: str
    source_message_id: str | None = None
    mark_read: bool = True
