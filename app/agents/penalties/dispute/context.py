"""Typed contract for the data handed to the dispute-summary LLM call.

Mirrors `app.agents.penalties.projection.context`'s shape: the mandatory
context carries only what every dispute summary needs to state the verdict
correctly (the verdict itself, the amounts, the reason code) -- rule detail,
the real facts the engine used, and this PO's prior-dispute history are all
supplementary, fetched only if the model decides they'd strengthen the
narrative, via `app.agents.penalties.dispute.tools`.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class DisputeOrderContext(BaseModel):
    order_id: str
    retailer_name: str
    sku_description: str


class DisputeSummaryContext(BaseModel):
    dispute_number: str
    reason_code: str
    claimed_amount: float
    computed_amount: float
    delta_amount: float
    verdict: str
    dispute_status: str
    analyzed_at: date
    order: DisputeOrderContext
