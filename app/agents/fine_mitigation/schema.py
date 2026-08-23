"""Schema for the output of the fine mitigation summary generation."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class FineMitigationSummaryOutput(BaseModel):
    order_id: str
    as_of_date: date
    prompt_version: str
    model_name: str
    summary: str
