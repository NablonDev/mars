"""Register and re-export all ORM models so they are available through `app.models`."""

from app.db.base import Base
from app.models.agent_registry import Agent, PromptVersion
from app.models.enums import JobItemStatus, JobRunType, JobTaskType, SummaryStatus
from app.models.fine_rule import FineRule, FineRuleTier
from app.models.fine_summaries import FineSummary
from app.models.fulfillment_facts import (
    DemandException,
    OrderConfirmation,
    ProductionSchedule,
    Shipment,
)
from app.models.job_queue import JobItem, JobRun
from app.models.master_data import Carrier, Location, Retailer, Sku
from app.models.order import Order
from app.models.outcomes import ActualFine, ProjectedFine

__all__ = [
    "ActualFine",
    "Agent",
    "Base",
    "Carrier",
    "DemandException",
    "FineRule",
    "FineRuleTier",
    "FineSummary",
    "JobItem",
    "JobItemStatus",
    "JobRun",
    "JobRunType",
    "JobTaskType",
    "Location",
    "Order",
    "OrderConfirmation",
    "ProductionSchedule",
    "ProjectedFine",
    "PromptVersion",
    "Retailer",
    "Shipment",
    "Sku",
    "SummaryStatus",
]
