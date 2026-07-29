from __future__ import annotations

from typing import Literal

from langgraph.types import interrupt

from cmir_agent.domain.models import CMIR, CMIRStatus, EmailMessage
from cmir_agent.domain.validators import CMIRValidator
from cmir_agent.interfaces.email_reader import EmailReader
from cmir_agent.interfaces.extractor import CMIRExtractor
from cmir_agent.interfaces.repositories import (
    ActionLogRepository,
    CMIRRepository,
    EmailRepository,
)
from cmir_agent.workflow.state import GraphState

ACTOR = "AI Agent"


class WorkflowNodes:
    """LangGraph node functions.

    Every method is a single, small step. All collaborators are injected
    (Dependency Inversion) so the graph never talks to IMAP, Postgres or
    Gemini directly - only through the ports defined in interfaces/.
    """

    def __init__(
        self,
        email_reader: EmailReader,
        extractor: CMIRExtractor,
        validator: CMIRValidator,
        email_repository: EmailRepository,
        cmir_repository: CMIRRepository,
        action_log_repository: ActionLogRepository,
    ) -> None:
        self._email_reader = email_reader
        self._extractor = extractor
        self._validator = validator
        self._email_repository = email_repository
        self._cmir_repository = cmir_repository
        self._action_log_repository = action_log_repository

    # ---- extraction / persistence steps ---- #

    def persist_email(self, state: GraphState) -> GraphState:
        email = EmailMessage(**state["email"])
        email_id = self._email_repository.save(email)
        self._action_log_repository.log(
            email_id, "Email Received", ACTOR, {"sender": email.sender}
        )
        return {"email_id": email_id}

    def extract_cmir(self, state: GraphState) -> GraphState:
        email = EmailMessage(**state["email"])
        cmir = self._extractor.extract(email.body)
        return {"cmir": cmir.model_dump()}

    def validate_cmir(self, state: GraphState) -> GraphState:
        cmir = CMIR(**state["cmir"])
        cmir = self._validator.validate(cmir)
        return {"cmir": cmir.model_dump()}

    def persist_ai_result(self, state: GraphState) -> GraphState:
        cmir = CMIR(**state["cmir"])
        self._email_repository.update_extraction(state["email_id"], cmir)

        action = (
            "Pending Human Action"
            if cmir.status == CMIRStatus.PENDING_HUMAN_ACTION.value
            else "Extraction Complete"
        )
        self._action_log_repository.log(
            state["email_id"], action, ACTOR, {"missing_fields": cmir.missing_fields}
        )
        return {}

    # ---- human-in-the-loop steps ---- #

    def collect_missing_fields(self, state: GraphState) -> GraphState:
        cmir_dict = state["cmir"]
        answers = interrupt(
            {
                "reason": "missing_mandatory_fields",
                "email_id": state["email_id"],
                "cmir": cmir_dict,
                "missing_fields": cmir_dict["missing_fields"],
            }
        )
        merged = CMIR(**cmir_dict)
        for field_name, value in answers.items():
            setattr(merged, field_name, value)
        return {"cmir": merged.model_dump()}

    def human_approval(self, state: GraphState) -> GraphState:
        decision = interrupt(
            {
                "reason": "approval_required",
                "email_id": state["email_id"],
                "cmir": state["cmir"],
            }
        )
        return {
            "decision": decision.get("decision"),
            "decision_reason": decision.get("reason", ""),
        }

    # ---- outcome steps ---- #

    def persist_cmir(self, state: GraphState) -> GraphState:
        cmir = CMIR(**state["cmir"])
        self._cmir_repository.save(state["email_id"], cmir)
        self._action_log_repository.log(
            state["email_id"], "CMIR Created", ACTOR, {"status": cmir.status}
        )
        return {}

    def persist_rejection(self, state: GraphState) -> GraphState:
        self._action_log_repository.log(
            state["email_id"],
            "CMIR Rejected",
            ACTOR,
            {"reason": state.get("decision_reason", "")},
        )
        return {}

    def mark_email_read(self, state: GraphState) -> GraphState:
        self._email_reader.mark_as_read(state["email"]["imap_id"])
        return {}

    # ---- routing functions ---- #

    def route_after_validation(self, state: GraphState) -> Literal["needs_input", "ready"]:
        missing = state["cmir"].get("missing_fields") or []
        return "needs_input" if missing else "ready"

    def route_after_approval(self, state: GraphState) -> Literal["approved", "rejected"]:
        return "approved" if state.get("decision") == "approve" else "rejected"
