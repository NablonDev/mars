from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict, Optional
from uuid import uuid4

from langgraph.types import Command

from cmir_agent.domain.models import CMIR, EmailMessage, PendingHumanAction
from cmir_agent.domain.validators import CMIRValidator
from cmir_agent.interfaces.email_reader import EmailReader
from cmir_agent.interfaces.observability import (
    AgentRunRepository,
    HITLActionRepository,
    HITLStateRepository,
    PendingHumanActionRepository,
    WorkflowThreadRepository,
)
from cmir_agent.interfaces.repositories import EmailRepository

logger = logging.getLogger(__name__)

INTERRUPT_KEY = "__interrupt__"

EDITABLE_FIELDS = {
    "sender_type",
    "customer_identity",
    "material_identity",
    "intent_phrase",
    "existing_cmir_ref",
    "brand",
    "site",
    "target_grd_code",
    "target_customer_material_ref",
    "effective_date",
    "reason",
}

STAGE_BY_INTERRUPT = {
    "missing_mandatory_fields": ("AWAITING_MISSING_FIELDS", "waiting_missing_fields"),
    "approval_required": ("AWAITING_APPROVAL", "waiting_approval"),
}

NODE_BY_INTERRUPT = {
    "missing_mandatory_fields": "collect_missing_fields",
    "approval_required": "review_extracted_cmir",
}

FINAL_STAGE_BY_DECISION = {
    "approve": ("COMPLETED_APPROVED", "completed_approved"),
    "reject": ("COMPLETED_REJECTED", "completed_rejected"),
}


class ServiceError(Exception):
    """Application error that maps cleanly to the PRD error contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class CMIRRunService:
    """Coordinates PRD API workflows across ports and LangGraph."""

    def __init__(
        self,
        *,
        email_reader: EmailReader,
        graph: Any,
        agent_runs: AgentRunRepository,
        workflow_threads: WorkflowThreadRepository,
        pending_human_actions: PendingHumanActionRepository,
        hitl_actions: HITLActionRepository,
        hitl_state: HITLStateRepository,
        email_repository: Optional[EmailRepository] = None,
        validator: Optional[CMIRValidator] = None,
    ) -> None:
        self._email_reader = email_reader
        self._graph = graph
        self._agent_runs = agent_runs
        self._workflow_threads = workflow_threads
        self._pending_human_actions = pending_human_actions
        self._hitl_actions = hitl_actions
        self._hitl_state = hitl_state
        self._email_repository = email_repository
        self._validator = validator or CMIRValidator()

    def start_email_ingest(
        self,
        *,
        max_workers: int = 4,
        subject_contains: Optional[str] = None,
        unread_only: bool = True,
    ) -> Dict[str, Any]:
        """Persist matching emails as queueable rows for the enqueuer function."""
        if self._email_repository is None:
            raise ServiceError(
                "QUEUE_NOT_CONFIGURED",
                "Email queue ingestion requires email repository.",
                status_code=500,
            )

        batch_id = self._new_batch_id()
        emails = self._email_reader.fetch_unread(
            subject_contains=subject_contains,
            unread_only=unread_only,
        )
        logger.info("Starting email ingest batch %s with %s fetched emails", batch_id, len(emails))

        threads = []
        for email in emails:
            row = self._email_repository.save_for_queue(email)
            threads.append(self._queue_summary(batch_id, row, row["queue_status"]))

        has_new = any(thread["status"] == "new" for thread in threads)

        return {
            "batch_id": batch_id,
            "status": "ready_for_queue" if has_new else "no_new_emails",
            "total_threads": len(threads),
            "threads": threads,
        }

    def process_queued_email(
        self,
        *,
        batch_id: str,
        email: Dict[str, Any],
        queue_message_id: str,
        email_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Process one Service Bus message through the existing CMIR workflow."""
        if email_id is None:
            email_id = email.get("email_id")
        if email_id is None:
            raise ServiceError(
                "VALIDATION_ERROR",
                "Queued email payload must include email_id.",
                status_code=422,
                details={"queue_message_id": queue_message_id},
            )

        if self._email_repository is not None:
            queue_state = self._email_repository.get_queue_state(email_id)
            if queue_state and queue_state.get("queue_status") == "processed":
                logger.info("Skipping already processed queued email %s", email_id)
                existing_thread = self._workflow_threads.get_by_email_id(email_id)
                if existing_thread is not None:
                    stage = self._workflow_threads.get_stage(existing_thread.thread_id)
                    if stage is not None:
                        return stage
                return self._already_processed_summary(batch_id, email_id)
            existing_thread = self._workflow_threads.get_by_email_id(email_id)
            if existing_thread is not None and existing_thread.status != "failed":
                self._email_repository.mark_queue_processed(email_id)
                stage = self._workflow_threads.get_stage(existing_thread.thread_id)
                if stage is not None:
                    return stage
                return self._already_processed_summary(batch_id, email_id)
            self._email_repository.mark_processing(email_id, queue_message_id)

        email_message = self._email_from_payload(email)
        try:
            result = self._process_email_thread(
                batch_id,
                email_message,
                existing_email_id=email_id,
                raise_on_error=True,
            )
            if self._email_repository is not None:
                self._email_repository.mark_queue_processed(email_id)
            return result
        except Exception as exc:
            if self._email_repository is not None:
                self._email_repository.mark_queue_failed(email_id, str(exc), retryable=False)
            raise

    def _process_email_thread(
        self,
        batch_id: str,
        email: Any,
        *,
        existing_email_id: Optional[Any] = None,
        raise_on_error: bool = False,
    ) -> Dict[str, Any]:
        """Run one email through its independent agent and LangGraph thread."""
        thread_id = self._new_thread_id()
        run_id = self._agent_runs.start(
            batch_id=batch_id,
            thread_id=thread_id,
            email_id=existing_email_id,
        )
        try:
            email_payload = self._email_to_payload(email)
            initial_state = {
                "batch_id": batch_id,
                "email": email_payload,
                "cmir": {},
                "decision": None,
                "run_id": run_id,
                "thread_id": thread_id,
            }
            if existing_email_id is not None:
                initial_state["email_id"] = existing_email_id
            state = self._graph.invoke(
                initial_state,
                config=self._thread_config(thread_id),
            )
            return self._handle_graph_state(run_id, thread_id, state)
        except Exception as exc:  # noqa: BLE001 - keep ingest batch moving per thread
            logger.exception("Workflow thread %s failed during ingest", thread_id)
            self._agent_runs.update_status(run_id, "failed", error=str(exc), completed=True)
            try:
                self._workflow_threads.update_status(
                    thread_id,
                    status="failed",
                    stage="FAILED",
                    error=str(exc),
                )
            except Exception:
                logger.warning("Failed before workflow thread row existed: %s", thread_id)
            if raise_on_error:
                raise
            return {
                "batch_id": batch_id,
                "agent_run_id": run_id,
                "thread_id": thread_id,
                "email_id": "",
                "stage": "FAILED",
                "status": "failed",
                "current_node": None,
                "pending_action_id": None,
                "updated_at": None,
            }

    def list_runs(
        self,
        *,
        view: str = "threads",
        batch_id: Optional[str] = None,
        agent_run_id: Optional[int] = None,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        sender: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return either reviewer thread rows or batch summaries."""
        if view == "threads":
            items, next_cursor = self._workflow_threads.list_threads(
                batch_id=batch_id,
                agent_run_id=agent_run_id,
                status=status,
                stage=stage,
                sender=sender,
                limit=limit,
                cursor=cursor,
            )
            return {"items": items, "next_cursor": next_cursor}
        if view == "agents":
            items, next_cursor = self._agent_runs.list_agents(
                batch_id=batch_id,
                status=status,
                limit=limit,
                cursor=cursor,
            )
            return {"items": items, "next_cursor": next_cursor}
        if view == "batches":
            items, next_cursor = self._agent_runs.list_batches(
                status=status,
                limit=limit,
                cursor=cursor,
            )
            return {"items": items, "next_cursor": next_cursor}
        raise ServiceError(
            "VALIDATION_ERROR",
            "view must be 'threads', 'agents', or 'batches'.",
            status_code=422,
            details={"view": view},
        )

    def get_stage(self, thread_id: str) -> Dict[str, Any]:
        """Return current UI stage for one thread."""
        stage = self._workflow_threads.get_stage(thread_id)
        if stage is None:
            raise self._thread_not_found(thread_id)
        return stage

    def get_snapshot(self, thread_id: str) -> Dict[str, Any]:
        """Return the latest review snapshot for one thread."""
        snapshot = self._workflow_threads.get_snapshot(thread_id)
        if snapshot is None:
            raise self._thread_not_found(thread_id)
        return snapshot

    def submit_missing_fields(
        self,
        thread_id: str,
        *,
        actor: str,
        fields: Dict[str, Any],
        expected_updated_at: str,
    ) -> Dict[str, Any]:
        """Resume a thread waiting for mandatory CMIR fields."""
        self._validate_fields(fields)
        stage = self._ensure_current(thread_id, expected_updated_at)
        if stage["status"] != "waiting_missing_fields":
            raise self._thread_not_waiting(thread_id, "waiting_missing_fields", stage["status"])

        pending = self._require_open_pending(thread_id, "missing_mandatory_fields")
        try:
            state = self._graph.invoke(Command(resume=fields), config=self._thread_config(thread_id))
        except Exception as exc:
            raise self._resume_failed(thread_id, pending["id"], exc) from exc
        try:
            return self._handle_graph_state(
                stage["agent_run_id"],
                thread_id,
                state,
                resume_context={
                    "pending_action_id": pending["id"],
                    "batch_id": stage["batch_id"],
                    "email_id": stage["email_id"],
                    "interrupt_type": "missing_mandatory_fields",
                    "question": pending["payload"],
                    "answer": fields,
                    "actor": actor,
                    "action_type": "field_update",
                    "field_changes": {name: {"from": "", "to": value} for name, value in fields.items()},
                },
            )
        except Exception as exc:
            raise self._resume_failed(thread_id, pending["id"], exc) from exc

    def update_draft(
        self,
        thread_id: str,
        *,
        actor: str,
        fields: Dict[str, Any],
        expected_updated_at: str,
    ) -> Dict[str, Any]:
        """Save reviewer edits while keeping the thread in approval review."""
        self._validate_fields(fields)
        stage = self._ensure_current(thread_id, expected_updated_at)
        if stage["status"] != "waiting_approval":
            raise self._thread_not_waiting(thread_id, "waiting_approval", stage["status"])

        snapshot = self.get_snapshot(thread_id)
        existing_cmir = snapshot.get("cmir", {})
        updated_cmir = {**existing_cmir, **fields}
        field_changes = {
            field: {"from": existing_cmir.get(field, ""), "to": value}
            for field, value in fields.items()
        }
        self._update_graph_cmir(thread_id, updated_cmir)

        pending = self._require_open_pending(thread_id, "approval_required")
        try:
            new_action_id = self._hitl_state.apply_human_action(
                run_id=stage["agent_run_id"],
                batch_id=stage["batch_id"],
                thread_id=thread_id,
                email_id=stage["email_id"],
                pending_action_id=pending["id"],
                interrupt_type="approval_required",
                question=pending["payload"],
                answer={"fields": fields},
                actor=actor,
                action_type="field_update",
                field_changes=field_changes,
                next_status="waiting_approval",
                next_stage="AWAITING_APPROVAL",
                next_current_node="review_extracted_cmir",
                next_latest_snapshot={"cmir": updated_cmir},
                next_cmir_status=updated_cmir.get("status"),
                next_pending_interrupt_type="approval_required",
                next_pending_payload={
                    "reason": "approval_required",
                    "email_id": stage["email_id"],
                    "cmir": updated_cmir,
                },
                next_pending_state_snapshot={"cmir": updated_cmir},
            )
        except Exception as exc:
            raise ServiceError(
                "WORKFLOW_RESUME_FAILED",
                "Unable to save reviewer update safely.",
                status_code=500,
                details={"thread_id": thread_id, "pending_action_id": pending["id"], "error": str(exc)},
            ) from exc
        return {
            "batch_id": stage["batch_id"],
            "agent_run_id": stage["agent_run_id"],
            "thread_id": thread_id,
            "stage": "AWAITING_APPROVAL",
            "status": "waiting_approval",
            "pending_action_id": new_action_id,
            "message": "Draft saved. Review again.",
        }

    def submit_decision(
        self,
        thread_id: str,
        *,
        actor: str,
        decision: str,
        expected_updated_at: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Approve or reject a thread waiting for approval."""
        if decision not in {"approve", "reject"}:
            raise ServiceError(
                "VALIDATION_ERROR",
                "decision must be 'approve' or 'reject'.",
                status_code=422,
                details={"decision": decision},
            )
        if decision == "reject" and not reason.strip():
            raise ServiceError(
                "VALIDATION_ERROR",
                "Reject requires reason.",
                status_code=422,
                details={"thread_id": thread_id},
            )

        stage = self._ensure_current(thread_id, expected_updated_at)
        if stage["status"] != "waiting_approval":
            raise self._thread_not_waiting(thread_id, "waiting_approval", stage["status"])

        pending = self._require_open_pending(thread_id, "approval_required")
        answer = {"decision": decision}
        if reason:
            answer["reason"] = reason
        try:
            state = self._graph.invoke(Command(resume=answer), config=self._thread_config(thread_id))
        except Exception as exc:
            raise self._resume_failed(thread_id, pending["id"], exc) from exc
        try:
            return self._handle_graph_state(
                stage["agent_run_id"],
                thread_id,
                state,
                resume_context={
                    "pending_action_id": pending["id"],
                    "batch_id": stage["batch_id"],
                    "email_id": stage["email_id"],
                    "interrupt_type": "approval_required",
                    "question": pending["payload"],
                    "answer": answer,
                    "actor": actor,
                    "action_type": "decision",
                    "decision": decision,
                    "reason": reason or None,
                },
            )
        except Exception as exc:
            raise self._resume_failed(thread_id, pending["id"], exc) from exc

    def _handle_graph_state(
        self,
        run_id: int,
        thread_id: str,
        state: Dict[str, Any],
        *,
        resume_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist thread status after a graph invoke or resume."""
        if state.get(INTERRUPT_KEY):
            payload = state[INTERRUPT_KEY][0].value
            reason = payload["reason"]
            stage, status = STAGE_BY_INTERRUPT[reason]
            if resume_context is None:
                action_id = self._pending_human_actions.create_open(
                    PendingHumanAction(
                        agent_run_id=run_id,
                        batch_id=state["batch_id"],
                        thread_id=thread_id,
                        email_id=str(state["email_id"]),
                        interrupt_type=reason,
                        payload=payload,
                        state_snapshot=self._snapshot_state(state),
                    )
                )
                self._workflow_threads.update_status(
                    thread_id,
                    status=status,
                    stage=stage,
                    current_node=NODE_BY_INTERRUPT[reason],
                    cmir_status=state.get("cmir", {}).get("status"),
                    latest_snapshot={"cmir": state.get("cmir", {})},
                    pending_action_id=action_id,
                )
                self._agent_runs.update_status(
                    run_id,
                    status,
                    email_id=state["email_id"],
                    current_node=NODE_BY_INTERRUPT[reason],
                )
            else:
                self._hitl_state.apply_human_action(
                    run_id=run_id,
                    batch_id=resume_context["batch_id"],
                    thread_id=thread_id,
                    email_id=resume_context["email_id"],
                    pending_action_id=resume_context["pending_action_id"],
                    interrupt_type=resume_context["interrupt_type"],
                    question=resume_context["question"],
                    answer=resume_context["answer"],
                    actor=resume_context["actor"],
                    action_type=resume_context["action_type"],
                    decision=resume_context.get("decision"),
                    reason=resume_context.get("reason"),
                    field_changes=resume_context.get("field_changes"),
                    next_status=status,
                    next_stage=stage,
                    next_current_node=NODE_BY_INTERRUPT[reason],
                    next_cmir_status=state.get("cmir", {}).get("status"),
                    next_latest_snapshot={"cmir": state.get("cmir", {})},
                    next_pending_interrupt_type=reason,
                    next_pending_payload=payload,
                    next_pending_state_snapshot=self._snapshot_state(state),
                )
            return self.get_stage(thread_id)

        decision = state.get("decision")
        stage, status = FINAL_STAGE_BY_DECISION.get(decision, ("COMPLETED_APPROVED", "completed_approved"))
        if resume_context is None:
            self._workflow_threads.update_status(
                thread_id,
                status=status,
                stage=stage,
                current_node=None,
                cmir_status=state.get("cmir", {}).get("status"),
                latest_snapshot={"cmir": state.get("cmir", {})},
                completed=True,
            )
            self._agent_runs.update_status(
                run_id,
                status,
                email_id=state.get("email_id"),
                completed=True,
            )
        else:
            self._hitl_state.apply_human_action(
                run_id=run_id,
                batch_id=resume_context["batch_id"],
                thread_id=thread_id,
                email_id=resume_context["email_id"],
                pending_action_id=resume_context["pending_action_id"],
                interrupt_type=resume_context["interrupt_type"],
                question=resume_context["question"],
                answer=resume_context["answer"],
                actor=resume_context["actor"],
                action_type=resume_context["action_type"],
                decision=resume_context.get("decision"),
                reason=resume_context.get("reason"),
                field_changes=resume_context.get("field_changes"),
                next_status=status,
                next_stage=stage,
                next_cmir_status=state.get("cmir", {}).get("status"),
                next_latest_snapshot={"cmir": state.get("cmir", {})},
                completed=True,
            )
        return self.get_stage(thread_id)

    def _require_open_pending(self, thread_id: str, interrupt_type: str) -> Dict[str, Any]:
        pending = self._pending_human_actions.get_open_for_thread(thread_id)
        if pending is None or pending["interrupt_type"] != interrupt_type:
            actual = None if pending is None else pending["interrupt_type"]
            raise self._thread_not_waiting(thread_id, interrupt_type, actual)
        return pending

    def _ensure_current(self, thread_id: str, expected_updated_at: str) -> Dict[str, Any]:
        stage = self.get_stage(thread_id)
        if stage["updated_at"] != expected_updated_at:
            raise ServiceError(
                "THREAD_STALE",
                "Thread was updated by another reviewer. Refresh snapshot and retry.",
                status_code=409,
                details={"thread_id": thread_id, "latest_updated_at": stage["updated_at"]},
            )
        return stage

    def _validate_fields(self, fields: Dict[str, Any]) -> None:
        invalid_fields = sorted(set(fields) - EDITABLE_FIELDS)
        if invalid_fields:
            raise ServiceError(
                "VALIDATION_ERROR",
                "Bad field name.",
                status_code=422,
                details={"invalid_fields": invalid_fields},
            )

    def _update_graph_cmir(self, thread_id: str, cmir: Dict[str, Any]) -> None:
        validated = self._validator.validate(CMIR(**cmir))
        if hasattr(self._graph, "update_state"):
            self._graph.update_state(self._thread_config(thread_id), {"cmir": validated.model_dump()})
            return
        logger.warning("Graph does not expose update_state; draft update saved only in repository")

    @staticmethod
    def _thread_config(thread_id: str) -> Dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def _email_to_payload(email: Any) -> Dict[str, Any]:
        if isinstance(email, dict):
            return email
        return asdict(email)

    @staticmethod
    def _email_from_payload(payload: Dict[str, Any]) -> EmailMessage:
        return EmailMessage(
            imap_id=str(payload.get("imap_id") or payload.get("source_imap_id") or payload["email_id"]),
            sender=payload["sender"],
            subject=payload["subject"],
            body=payload.get("body") or payload.get("raw_content") or "",
            source_message_id=payload.get("source_message_id"),
            mark_read=bool(payload.get("mark_read", True)),
        )

    @staticmethod
    def _already_processed_summary(batch_id: str, email_id: Any) -> Dict[str, Any]:
        return {
            "batch_id": batch_id,
            "agent_run_id": 0,
            "thread_id": "",
            "email_id": str(email_id),
            "stage": "ALREADY_PROCESSED",
            "status": "already_processed",
            "current_node": None,
            "pending_action_id": None,
            "updated_at": None,
        }

    @staticmethod
    def _queue_summary(batch_id: str, row: Dict[str, Any], status: str) -> Dict[str, Any]:
        stage_by_status = {
            "new": "NEW",
            "queued": "QUEUED",
            "enqueueing": "ENQUEUEING",
            "processing": "PROCESSING",
            "processed": "ALREADY_PROCESSED",
            "failed": "FAILED",
            "queue_failed": "QUEUE_FAILED",
        }
        return {
            "batch_id": batch_id,
            "agent_run_id": 0,
            "thread_id": "",
            "email_id": str(row["id"]),
            "source_message_id": row.get("source_message_id"),
            "sender": row.get("sender"),
            "subject": row.get("subject"),
            "stage": stage_by_status.get(status, "INGESTED"),
            "status": status,
            "current_node": None,
            "pending_action_id": None,
            "updated_at": None,
        }

    @staticmethod
    def _new_thread_id() -> str:
        return f"thread_{uuid4().hex[:12]}"

    @staticmethod
    def _new_batch_id() -> str:
        return f"batch_{uuid4().hex[:12]}"

    @staticmethod
    def _snapshot_state(state: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in state.items() if key != INTERRUPT_KEY}

    @staticmethod
    def _thread_not_found(thread_id: str) -> ServiceError:
        return ServiceError(
            "THREAD_NOT_FOUND",
            "Unknown thread_id.",
            status_code=404,
            details={"thread_id": thread_id},
        )

    @staticmethod
    def _resume_failed(thread_id: str, pending_action_id: int, exc: Exception) -> ServiceError:
        logger.exception(
            "Failed to resume workflow thread %s from pending action %s",
            thread_id,
            pending_action_id,
        )
        return ServiceError(
            "WORKFLOW_RESUME_FAILED",
            "Unexpected failure while resuming workflow.",
            status_code=500,
            details={"thread_id": thread_id, "pending_action_id": pending_action_id, "error": str(exc)},
        )

    @staticmethod
    def _thread_not_waiting(
        thread_id: str,
        expected: str,
        actual: Optional[str],
    ) -> ServiceError:
        return ServiceError(
            "THREAD_NOT_WAITING",
            "Resume API called while thread is not paused for that action.",
            status_code=409,
            details={"thread_id": thread_id, "expected": expected, "actual": actual},
        )
