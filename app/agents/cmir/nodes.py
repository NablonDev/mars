from __future__ import annotations

from typing import Literal, Optional

from langgraph.types import interrupt

from app.agents.cmir.state import GraphState
from app.repositories.action_log import PostgresActionLogRepository
from app.repositories.cmir import CMIRVersionConflict, PostgresCMIRRepository
from app.repositories.email import PostgresEmailRepository
from app.repositories.observability import PostgresWorkflowThreadRepository
from app.schemas.cmir import CMIR, CMIRStatus, EmailMessage, WorkflowThread
from app.services.cmir_extractor import AzureOpenAICMIRExtractor
from app.services.cmir_merge import merge_with_active
from app.services.cmir_validation import CMIRValidator
from app.services.email_reader import GmailImapReader

ACTOR = "AI Agent"


class WorkflowNodes:
    """LangGraph node functions.

    Every method is a single, small step. All collaborators are injected
    (Dependency Inversion) so the graph never talks to IMAP, Postgres or
    Azure OpenAI directly - only through the collaborators passed in here.
    """

    def __init__(
        self,
        email_reader: GmailImapReader,
        extractor: AzureOpenAICMIRExtractor,
        validator: CMIRValidator,
        email_repository: PostgresEmailRepository,
        cmir_repository: PostgresCMIRRepository,
        action_log_repository: PostgresActionLogRepository,
        workflow_thread_repository: Optional[PostgresWorkflowThreadRepository] = None,
    ) -> None:
        self._email_reader = email_reader
        self._extractor = extractor
        self._validator = validator
        self._email_repository = email_repository
        self._cmir_repository = cmir_repository
        self._action_log_repository = action_log_repository
        self._workflow_thread_repository = workflow_thread_repository

    # ---- extraction / persistence steps ---- #

    def persist_email(self, state: GraphState) -> GraphState:
        email = EmailMessage(**state["email"])
        email_id = state.get("email_id")
        if email_id is None:
            email_id = self._email_repository.save(email)
        thread_id = state.get("thread_id")
        if thread_id and self._workflow_thread_repository is not None:
            self._workflow_thread_repository.create(
                WorkflowThread(
                    thread_id=thread_id,
                    agent_run_id=state["run_id"],
                    batch_id=state["batch_id"],
                    email_id=str(email_id),
                    source_message_id=email.source_message_id,
                    sender=email.sender,
                    subject=email.subject,
                    status="running",
                    current_node="persist_email",
                    stage="INGESTING",
                    latest_snapshot={"email": state["email"], "cmir": state.get("cmir", {})},
                )
            )
        self._action_log_repository.log(
            email_id, "Email Received", ACTOR, {"sender": email.sender}
        )
        return {"email_id": email_id}

    def extract_cmir(self, state: GraphState) -> GraphState:
        email = EmailMessage(**state["email"])
        cmir = self._extractor.extract(email.body)
        return {"cmir": cmir.model_dump()}

    def identify_existing_cmir(self, state: GraphState) -> GraphState:
        """Look up the current active cmir_records row for this entity, if any.

        Runs on every pass through this part of the graph, including a loop back
        from collect_missing_fields -- a mandatory field (e.g. customer_identity)
        may only become known once the human supplies it, so re-running this lookup
        is what lets prepare_diff/validate_cmir see an accurate existing record and
        diff instead of one computed against a blank identity.
        """
        cmir = CMIR(**state["cmir"])
        existing = self._cmir_repository.get_current(
            cmir.customer_identity, cmir.target_customer_material_ref
        )
        return {"existing_cmir": existing}

    def prepare_diff(self, state: GraphState) -> GraphState:
        """Merge the proposed draft onto the active record (if any) and stash the diff.

        Always runs, whether this turns out to be a create (existing_cmir is None,
        diff is "from blank") or an update -- this keeps human_approval's interrupt
        payload uniform regardless of which case a given email falls into. The
        existing record's id becomes the version token persist_cmir will later use
        to detect a conflicting concurrent write.
        """
        existing = state.get("existing_cmir")
        proposed = CMIR(**state["cmir"])
        merged, diff = merge_with_active(existing, proposed)
        return {
            "cmir": merged.model_dump(),
            "cmir_diff": diff,
            "cmir_version_token": existing["id"] if existing else None,
        }

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
                "existing_cmir": state.get("existing_cmir"),
                "diff": state.get("cmir_diff", {}),
            }
        )
        return {
            "decision": decision.get("decision"),
            "decision_reason": decision.get("reason", ""),
        }

    # ---- outcome steps ---- #

    def persist_cmir(self, state: GraphState) -> GraphState:
        """Attempt to commit the approved (merged) draft as the new current version.

        Does not raise on a version conflict -- it catches CMIRVersionConflict and
        reports the outcome through cmir_write_result instead, so
        route_after_persist_cmir can send the graph to handle_version_conflict rather
        than crashing the run. cmir_version_token is whatever prepare_diff captured
        the active record's id as when the diff was computed; supersede_and_insert
        re-checks it (and, as the real backstop, the database's own partial unique
        index) at commit time.
        """
        cmir = CMIR(**state["cmir"])
        try:
            self._cmir_repository.supersede_and_insert(
                customer_identity=cmir.customer_identity,
                target_customer_material_ref=cmir.target_customer_material_ref,
                merged=cmir,
                expected_current_id=state.get("cmir_version_token"),
            )
        except CMIRVersionConflict:
            return {"cmir_write_result": "conflict"}
        self._action_log_repository.log(
            state["email_id"], "CMIR Created", ACTOR, {"status": cmir.status}
        )
        return {"cmir_write_result": "committed"}

    def persist_rejection(self, state: GraphState) -> GraphState:
        self._action_log_repository.log(
            state["email_id"],
            "CMIR Rejected",
            ACTOR,
            {"reason": state.get("decision_reason", "")},
        )
        return {}

    def handle_version_conflict(self, state: GraphState) -> GraphState:
        """Record that persist_cmir's write was rejected by a concurrent update.

        Reached only via a routing decision after persist_cmir catches
        CMIRVersionConflict (see app.repositories.cmir) -- this node itself doesn't
        need to know how that was detected, only that it happened, so it stays
        decoupled from persist_cmir's exact implementation.
        """
        cmir = state["cmir"]
        self._action_log_repository.log(
            state["email_id"],
            "CMIR Version Conflict",
            ACTOR,
            {
                "customer_identity": cmir.get("customer_identity"),
                "target_customer_material_ref": cmir.get("target_customer_material_ref"),
            },
        )
        return {}

    def mark_email_read(self, state: GraphState) -> GraphState:
        if not state["email"].get("mark_read", True):
            return {}
        self._email_reader.mark_as_read(state["email"]["imap_id"])
        return {}

    # ---- routing functions ---- #

    def route_after_validation(self, state: GraphState) -> Literal["needs_input", "ready"]:
        missing = state["cmir"].get("missing_fields") or []
        return "needs_input" if missing else "ready"

    def route_after_approval(self, state: GraphState) -> Literal["approved", "rejected"]:
        return "approved" if state.get("decision") == "approve" else "rejected"

    def route_after_persist_cmir(self, state: GraphState) -> Literal["committed", "conflict"]:
        return state.get("cmir_write_result", "committed")
