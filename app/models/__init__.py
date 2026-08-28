"""Register and re-export all ORM models so they are available through `app.models`."""

from app.db.base import Base, TimestampMixin
from app.models.agent_registry import Agent, PromptVersion
from app.models.cmir import CMIRRecordORM
from app.models.email import EmailActionLogORM, EmailEventORM
from app.models.enums import JobItemStatus, JobRunType, JobTaskType, SummaryStatus
from app.models.fine_master_data import Carrier, Location, Retailer, Sku
from app.models.fine_mitigation.mitigation import MitigationInput
from app.models.fine_mitigation.result import MitigationResult
from app.models.fine_mitigation.summary import MitigationSummary
from app.models.fine_projection.fulfillment_facts import (
    DemandException,
    OrderConfirmation,
    ProductionSchedule,
    Shipment,
)
from app.models.fine_projection.outcomes import ActualFine, ProjectedFine
from app.models.fine_projection.po_delivery_change_request import PoDeliveryChangeRequest
from app.models.fine_projection.summary import ProjectionSummary
from app.models.fine_rule import FineRule, FineRuleTier
from app.models.job_queue import JobItem, JobRun
from app.models.observability import (
    AgentRunORM,
    AgentTraceORM,
    HITLActionORM,
    PendingHumanActionORM,
    WorkflowThreadORM,
)
from app.models.order import Order
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
    "HITLActionORM",
    "JobItem",
    "JobItemStatus",
    "JobRun",
    "JobRunType",
    "JobTaskType",
    "Location",
    "MaterialMasterORM",
    "MitigationInput",
    "MitigationResult",
    "MitigationSummary",
    "Order",
    "OrderConfirmation",
    "PendingHumanActionORM",
    "PoDeliveryChangeRequest",
    "PoLineErrorORM",
    "PoLineORM",
    "ProductionSchedule",
    "ProjectedFine",
    "ProjectionSummary",
    "PromptVersion",
    "Retailer",
    "Shipment",
    "Sku",
    "SummaryStatus",
    "TimestampMixin",
    "WorkflowThreadORM",
]
