from __future__ import annotations

from cmir_agent.domain.models import CMIR, CMIRStatus, MANDATORY_FIELDS


class CMIRValidator:
    """Single source of truth for the "is this CMIR complete?" business rule.

    The LLM extractor is intentionally not trusted to self-report status:
    a validator that doesn't depend on the model's own judgement is easier
    to test, audit and change independently of prompt/model choices
    (Single Responsibility + Open/Closed).
    """

    def __init__(self, mandatory_fields: tuple = MANDATORY_FIELDS) -> None:
        self._mandatory_fields = mandatory_fields

    def validate(self, cmir: CMIR) -> CMIR:
        missing = [
            field_name
            for field_name in self._mandatory_fields
            if not str(getattr(cmir, field_name, "")).strip()
        ]

        cmir.missing_fields = missing
        cmir.status = (
            CMIRStatus.PENDING_HUMAN_ACTION.value
            if missing
            else CMIRStatus.PENDING_REVIEW.value
        )
        return cmir
