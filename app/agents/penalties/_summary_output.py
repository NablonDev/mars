"""Shared base for the two penalty-summary features' `*Output` schemas --
`PenaltyProjectionSummaryOutput`/`PenaltyMitigationSummaryOutput` are
otherwise field-for-field identical. Exists so
`app.services.penalties._summary_base.SummaryServiceBase`'s `OutputT`
generic bound can rely on `model_name`/`summary` existing, which a bare
`BaseModel` bound can't guarantee.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class PenaltySummaryOutputBase(BaseModel):
    order_id: str
    as_of_date: date
    prompt_version: str
    model_name: str
    summary: str
