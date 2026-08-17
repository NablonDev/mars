"""API schemas for fine-summary requests and responses."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FineSummaryRequest(BaseModel):
    as_of_date: date | None = None
    force_regenerate: bool = False


class FineSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    as_of_date: date
    prompt_version: str
    model_name: str
    summary: str


class FineSummaryStatusResponse(BaseModel):
    order_id: str
    as_of_date: date
    prompt_version: str
    status: Literal["PENDING", "READY", "FAILED"]
    summary: FineSummaryResponse | None = None
    error_message: str | None = None
