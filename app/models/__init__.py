"""
Re-exports every ORM model so that a single `import app.models` (or
any of the names below) registers the whole schema on `Base.metadata` --
callers (alembic/env.py, Database.create_all_tables, tests) don't need to
know which of the per-entity modules a given table lives in.
"""

from app.db.base import Base
from app.models.explanations import ProjectionExplanation
from app.models.fine_rule import FineRuleORM, FineRuleTier
from app.models.fulfillment_facts import (
    DemandException,
    OrderConfirmation,
    ProductionSchedule,
    Shipment,
)
from app.models.master_data import Carrier, Location, Retailer, Sku
from app.models.order import OrderORM
from app.models.outcomes import ActualFine, ProjectedFine

__all__ = [
    "ActualFine",
    "Base",
    "Carrier",
    "DemandException",
    "FineRuleORM",
    "FineRuleTier",
    "Location",
    "OrderConfirmation",
    "OrderORM",
    "ProductionSchedule",
    "ProjectedFine",
    "ProjectionExplanation",
    "Retailer",
    "Shipment",
    "Sku",
]
