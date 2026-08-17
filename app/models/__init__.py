"""Register and re-export all ORM models so they are available through `app.models`."""

from app.db.base import Base
from app.models.agent_registry import Agent, PromptVersion
from app.models.cmir import CMIRRecordORM
from app.models.email import EmailActionLogORM, EmailEventORM
from app.models.fine_rule import FineRule, FineRuleTier
from app.models.fine_summaries import FineSummary
from app.models.fulfillment_facts import (
    DemandException,
    OrderConfirmation,
    ProductionSchedule,
    Shipment,
)
from app.models.master_data import Carrier, Location, Retailer, Sku
from app.models.observability import (
    AgentRunORM,
    AgentTraceORM,
    HITLActionORM,
    PendingHumanActionORM,
    WorkflowThreadORM,
)
from app.models.order import Order
from app.models.outcomes import ActualFine, ProjectedFine
from app.models.po_validation import MaterialMasterORM, PoLineErrorORM, PoLineORM

__all__ = [
    "ActualFine",
    "Agent",
    "AgentRunORM",
    "AgentTraceORM",
    "Base",
    "CMIRRecordORM",
    "Carrier",
    "DemandException",
    "EmailActionLogORM",
    "EmailEventORM",
    "FineRule",
    "FineRuleTier",
    "FineSummary",
    "HITLActionORM",
    "Location",
    "MaterialMasterORM",
    "Order",
    "OrderConfirmation",
    "PendingHumanActionORM",
    "PoLineErrorORM",
    "PoLineORM",
    "ProductionSchedule",
    "ProjectedFine",
    "PromptVersion",
    "Retailer",
    "Shipment",
    "Sku",
    "WorkflowThreadORM",
]
