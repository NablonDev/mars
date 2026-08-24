"""API schemas for fine-projection-summary requests and responses."""

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.models.enums import SummaryStatus


class ProjectionSummaryRequest(BaseModel):
    as_of_date: date | None = None
    force_regenerate: bool = False


class ProjectionSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    as_of_date: date
    prompt_version: str
    model_name: str
    summary: str
    # Reuse disclosure; defaults preserve the non-reused response contract.
    is_reused: bool = False
    generated_for_date: date | None = None
    unchanged_since: date | None = None
    unchanged_for_days: int | None = None


class ProjectionSummaryStatusResponse(BaseModel):
    order_id: str
    as_of_date: date
    prompt_version: str
    status: SummaryStatus
    summary: ProjectionSummaryResponse | None = None
    error_message: str | None = None
