from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from langgraph.types import Command

from app.core.exceptions import ServiceError
from app.repositories.observability import (
    PostgresAgentRunRepository,
    PostgresHITLActionRepository,
    PostgresHITLStateRepository,
    PostgresPendingHumanActionRepository,
    PostgresWorkflowThreadRepository,
)
from app.repositories.po_validation import (
    PostgresMaterialMasterRepository,
    PostgresPoLineErrorRepository,
    PostgresPoLineRepository,
)
from app.schemas.cmir import PendingHumanAction, WorkflowThread
from app.schemas.po_validation import PoLine
from app.services.cmir_run_service import INTERRUPT_KEY

logger = logging.getLogger(__name__)

STAGE_BY_INTERRUPT = {
    "manual_cmir_entry": ("AWAITING_MANUAL_CMIR_ENTRY", "waiting_manual_cmir_entry"),
    "qty_mismatch_decision": ("AWAITING_QTY_MISMATCH_DECISION", "waiting_qty_mismatch_decision"),
}

NODE_BY_INTERRUPT = {
    "manual_cmir_entry": "human_manual_cmir_entry",
    "qty_mismatch_decision": "human_qty_mismatch_decision",
}

# Maps the terminal po_lines.status set by the graph's outcome nodes to the
# PRD §10 UI stage / workflow_threads.status pair.
FINAL_STAGE_BY_PO_STATUS = {
    "READY_FOR_SO_CREATION": ("READY_FOR_SO_CREATION", "ready_for_so_creation"),
    "READY_FOR_SO_CREATION_PARTIAL": ("READY_FOR_SO_CREATION_PARTIAL", "ready_for_so_creation_partial"),
    "DISCONTINUED": ("COMPLETED_DISCONTINUED", "completed_discontinued"),
    "FAILED": ("FAILED", "failed"),
}


class PoValidationService:
    """Coordinates the PO Validation Agent's API workflows across repositories and LangGraph.

    Mirrors app.services.cmir_run_service.CMIRRunService: same resume-then-persist
    pattern via PostgresHITLStateRepository, same ServiceError contract, same
    graph.invoke/Command(resume=...) usage. The one structural difference is that a
    workflow_threads row (and therefore a reviewer-facing thread_id) is only created
    lazily, the first time a PO line actually interrupts -- not eagerly for every
    line, since the PRD requires the fully automatic path to never create a
    thread_id at all.
    """

    def __init__(
        self,
        *,
        graph: Any,
        po_lines: PostgresPoLineRepository,
        material_master: PostgresMaterialMasterRepository,
        po_line_errors: PostgresPoLineErrorRepository,
        agent_runs: PostgresAgentRunRepository,
        workflow_threads: PostgresWorkflowThreadRepository,
        pending_human_actions: PostgresPendingHumanActionRepository,
        hitl_actions: PostgresHITLActionRepository,
        hitl_state: PostgresHITLStateRepository,
    ) -> None:
        self._graph = graph
        self._po_lines = po_lines
        self._material_master = material_master
        self._po_line_errors = po_line_errors
        self._agent_runs = agent_runs
        self._workflow_threads = workflow_threads
        self._pending_human_actions = pending_human_actions
        self._hitl_actions = hitl_actions
        self._hitl_state = hitl_state

    def ingest_po_lines(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist each PO line and run it through the graph synchronously.

        There is no queue/internal-process endpoint for this agent per the PRD's API
        contract (§11.1 has no equivalent of CMIR's /internal/process-email), so each
        line is validated inline as part of the ingest request, same style as
        CMIRRunService._process_email_thread.
        """
        batch_id = self._new_batch_id()
        summaries = [self._ingest_one_line(batch_id, payload) for payload in lines]
        return {"batch_id": batch_id, "total_lines": len(summaries), "lines": summaries}

    def _ingest_one_line(self, batch_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        po_line = PoLine(
            batch_id=batch_id,
            po_number=payload["po_number"],
            po_line_number=payload["po_line_number"],
            customer_id=payload["customer_id"],
            customer_material_code=payload["customer_material_code"],
            plant=payload["plant"],
            order_quantity=payload["order_quantity"],
            uom=payload.get("uom"),
            requested_delivery_date=payload.get("requested_delivery_date"),
            raw_payload=payload,
        )
        po_line_id = self._po_lines.create(po_line)
        result = self._run_po_line(batch_id, po_line_id, po_line)
        # po_lines.status (NEW/AWAITING_DECISION/READY_FOR_SO_CREATION/...) is the
        # vocabulary this response reports in, not workflow_threads.status (which
        # `result` carries and is only meaningful once a thread exists) -- read it
        # back directly so a touchless line reports correctly even with no thread.
        current = self._po_lines.get(po_line_id)
        return {
            "po_line_id": str(po_line_id),
            "batch_id": batch_id,
            "po_number": po_line.po_number,
            "po_line_number": po_line.po_line_number,
            "status": current["status"] if current else result["status"],
            "thread_id": result.get("thread_id"),
            "updated_at": result.get("updated_at"),
        }

    def _run_po_line(self, batch_id: str, po_line_id: Any, po_line: PoLine) -> dict[str, Any]:
        checkpoint_thread_id = self._new_checkpoint_thread_id()
        run_id = self._agent_runs.start(batch_id=batch_id, po_line_id=po_line_id, run_type="PO_VALIDATION")
        initial_state = {
            "batch_id": batch_id,
            "run_id": run_id,
            "po_line_id": po_line_id,
            "thread_id": checkpoint_thread_id,
            "po_line": {
                "po_number": po_line.po_number,
                "po_line_number": po_line.po_line_number,
                "customer_id": po_line.customer_id,
                "customer_material_code": po_line.customer_material_code,
                "plant": po_line.plant,
                "order_quantity": po_line.order_quantity,
                "uom": po_line.uom,
            },
        }
        try:
            state = self._graph.invoke(initial_state, config=self._thread_config(checkpoint_thread_id))
        except Exception as exc:
            logger.exception("PO line %s failed during ingest", po_line_id)
            self._agent_runs.update_status(run_id, "failed", error=str(exc), completed=True)
            self._po_lines.update_status(po_line_id, "FAILED")
            return {"batch_id": batch_id, "thread_id": None, "status": "FAILED", "updated_at": None}
        return self._handle_graph_state(run_id, batch_id, po_line_id, checkpoint_thread_id, state)

    def list_ready_lines(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        try:
            items, next_cursor = self._po_lines.list_by_status(status=status, limit=limit, cursor=cursor)
        except ValueError as exc:
            raise ServiceError(
                "VALIDATION_ERROR",
                "cursor must be an ISO 8601 timestamp, as returned in next_cursor.",
                status_code=422,
                details={"cursor": cursor},
            ) from exc
        return {"items": items, "next_cursor": next_cursor}

    def get_errors(self, po_line_id: Any) -> dict[str, Any]:
        po_line = self._po_lines.get(po_line_id)
        if po_line is None:
            raise ServiceError(
                "VALIDATION_ERROR",
                "Unknown po_line_id.",
                status_code=422,
                details={"po_line_id": str(po_line_id)},
            )
        return {"items": self._po_line_errors.list_for_po_line(po_line_id)}

    def get_stage(self, thread_id: str) -> dict[str, Any]:
        stage = self._workflow_threads.get_stage(thread_id)
        if stage is None:
            raise self._thread_not_found(thread_id)
        return stage

    def get_snapshot(self, thread_id: str) -> dict[str, Any]:
        stage = self.get_stage(thread_id)
        po_line_id = stage.get("po_line_id")
        po_line = self._po_lines.get(po_line_id) if po_line_id else None
        if po_line is None:
            raise self._thread_not_found(thread_id)

        pending = self._pending_human_actions.get_open_for_thread(thread_id)
        candidate: dict[str, Any] | None = None
        editable_fields: list[str] = []
        if pending is not None:
            payload = pending["payload"]
            if pending["interrupt_type"] == "qty_mismatch_decision":
                candidate = payload.get("candidate")
                editable_fields = ["substitute_material_code"]
            elif pending["interrupt_type"] == "manual_cmir_entry":
                editable_fields = ["sap_material_number", "description"]

        return {
            "agent_run_id": stage["agent_run_id"],
            "thread_id": thread_id,
            "po_line_id": po_line_id,
            "po_number": po_line["po_number"],
            "po_line_number": po_line["po_line_number"],
            "customer_material_code": po_line["customer_material_code"],
            "order_quantity": po_line["order_quantity"],
            "stage": stage["stage"],
            "candidate": candidate,
            "editable_fields": editable_fields,
            "history": self._hitl_actions.list_for_thread(thread_id),
            "updated_at": stage["updated_at"],
        }

    def submit_manual_cmir_entry(
        self,
        thread_id: str,
        *,
        actor: str,
        sap_material_number: str,
        description: str = "",
        expected_updated_at: str,
    ) -> dict[str, Any]:
        """Resume a thread waiting for a manually entered CMIR mapping."""
        stage = self._ensure_current(thread_id, expected_updated_at)
        if stage["status"] != "waiting_manual_cmir_entry":
            raise self._thread_not_waiting(thread_id, "waiting_manual_cmir_entry", stage["status"])

        po_line = self._po_lines.get(stage["po_line_id"])
        if po_line is None:
            raise self._thread_not_found(thread_id)
        self._require_material(sap_material_number, po_line["plant"])

        pending = self._require_open_pending(thread_id, "manual_cmir_entry")
        answer = {"sap_material_number": sap_material_number, "description": description}
        try:
            state = self._graph.invoke(Command(resume=answer), config=self._thread_config(thread_id))
        except Exception as exc:
            raise self._resume_failed(thread_id, pending["id"], exc) from exc
        try:
            return self._handle_graph_state(
                stage["agent_run_id"],
                stage["batch_id"],
                stage["po_line_id"],
                thread_id,
                state,
                resume_context={
                    "pending_action_id": pending["id"],
                    "interrupt_type": "manual_cmir_entry",
                    "question": pending["payload"],
                    "answer": answer,
                    "actor": actor,
                    "action_type": "manual_entry",
                    "field_changes": {"sap_material_number": {"from": "", "to": sap_material_number}},
                },
            )
        except Exception as exc:
            raise self._resume_failed(thread_id, pending["id"], exc) from exc

    def submit_qty_mismatch_decision(
        self,
        thread_id: str,
        *,
        actor: str,
        decision: str,
        substitute_material_code: str | None = None,
        expected_updated_at: str,
    ) -> dict[str, Any]:
        """Resume a thread waiting for a quantity-mismatch resolution."""
        if decision not in {"use_substitute", "proceed_anyway", "mark_stale"}:
            raise ServiceError(
                "VALIDATION_ERROR",
                "decision must be 'use_substitute', 'proceed_anyway', or 'mark_stale'.",
                status_code=422,
                details={"decision": decision},
            )

        stage = self._ensure_current(thread_id, expected_updated_at)
        if stage["status"] != "waiting_qty_mismatch_decision":
            raise self._thread_not_waiting(thread_id, "waiting_qty_mismatch_decision", stage["status"])

        pending = self._require_open_pending(thread_id, "qty_mismatch_decision")
        answer: dict[str, Any] = {"decision": decision}
        if decision == "use_substitute":
            po_line = self._po_lines.get(stage["po_line_id"])
            if po_line is None:
                raise self._thread_not_found(thread_id)
            candidate = pending["payload"].get("candidate", {})
            chosen = substitute_material_code or candidate.get("suggested_substitute_material_code")
            if not chosen:
                raise ServiceError(
                    "VALIDATION_ERROR",
                    "use_substitute requires substitute_material_code, since no suggestion was offered.",
                    status_code=422,
                    details={"thread_id": thread_id},
                )
            self._require_material(chosen, po_line["plant"])
            answer["substitute_material_code"] = chosen

        try:
            state = self._graph.invoke(Command(resume=answer), config=self._thread_config(thread_id))
        except Exception as exc:
            raise self._resume_failed(thread_id, pending["id"], exc) from exc
        try:
            return self._handle_graph_state(
                stage["agent_run_id"],
                stage["batch_id"],
                stage["po_line_id"],
                thread_id,
                state,
                resume_context={
                    "pending_action_id": pending["id"],
                    "interrupt_type": "qty_mismatch_decision",
                    "question": pending["payload"],
                    "answer": answer,
                    "actor": actor,
                    "action_type": "decision",
                    "decision": decision,
                },
            )
        except Exception as exc:
            raise self._resume_failed(thread_id, pending["id"], exc) from exc

    def _handle_graph_state(
        self,
        run_id: UUID,
        batch_id: str,
        po_line_id: Any,
        checkpoint_thread_id: str,
        state: dict[str, Any],
        *,
        resume_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist po_line/thread status after a graph invoke or resume."""
        if state.get(INTERRUPT_KEY):
            payload = state[INTERRUPT_KEY][0].value
            reason = payload["reason"]
            stage, status = STAGE_BY_INTERRUPT[reason]

            if resume_context is None:
                # First interrupt for this line: this is the only point at which a
                # reviewer-facing thread is created, per the PRD's "no thread_id for
                # the automatic path" rule.
                self._workflow_threads.create(
                    WorkflowThread(
                        thread_id=checkpoint_thread_id,
                        agent_run_id=run_id,
                        batch_id=batch_id,
                        email_id=None,
                        source_message_id=None,
                        sender=str(payload.get("customer_id", "")),
                        subject=f"PO {payload.get('po_number', '')} line {payload.get('po_line_number', '')}",
                        status=status,
                        current_node=NODE_BY_INTERRUPT[reason],
                        stage=stage,
                        latest_snapshot={"po_line_id": str(po_line_id), "payload": payload},
                        po_line_id=po_line_id,
                    )
                )
                action_id = self._pending_human_actions.create_open(
                    PendingHumanAction(
                        agent_run_id=run_id,
                        batch_id=batch_id,
                        thread_id=checkpoint_thread_id,
                        email_id=None,
                        interrupt_type=reason,
                        payload=payload,
                        state_snapshot=self._snapshot_state(state),
                        po_line_id=po_line_id,
                    )
                )
                self._workflow_threads.update_status(
                    checkpoint_thread_id,
                    status=status,
                    stage=stage,
                    current_node=NODE_BY_INTERRUPT[reason],
                    latest_snapshot={"po_line_id": str(po_line_id), "payload": payload},
                    pending_action_id=action_id,
                )
                self._agent_runs.update_status(run_id, status, current_node=NODE_BY_INTERRUPT[reason])
            else:
                self._hitl_state.apply_human_action(
                    run_id=run_id,
                    batch_id=batch_id,
                    thread_id=checkpoint_thread_id,
                    email_id=None,
                    pending_action_id=resume_context["pending_action_id"],
                    interrupt_type=resume_context["interrupt_type"],
                    question=resume_context["question"],
                    answer=resume_context["answer"],
                    actor=resume_context["actor"],
                    action_type=resume_context["action_type"],
                    field_changes=resume_context.get("field_changes"),
                    decision=resume_context.get("decision"),
                    next_status=status,
                    next_stage=stage,
                    next_current_node=NODE_BY_INTERRUPT[reason],
                    next_latest_snapshot={"po_line_id": str(po_line_id), "payload": payload},
                    next_pending_interrupt_type=reason,
                    next_pending_payload=payload,
                    next_pending_state_snapshot=self._snapshot_state(state),
                    po_line_id=po_line_id,
                )
            self._po_lines.update_status(po_line_id, "AWAITING_DECISION")
            return self.get_stage(checkpoint_thread_id)

        # No interrupt: the graph's outcome node already set the terminal po_lines.status.
        final_po_line = self._po_lines.get(po_line_id)
        final_status = final_po_line["status"] if final_po_line else "FAILED"
        stage, status = FINAL_STAGE_BY_PO_STATUS.get(final_status, ("FAILED", "failed"))

        if resume_context is None:
            # Touchless path: no thread was ever created for this line.
            self._agent_runs.update_status(run_id, status, completed=True)
            return {
                "batch_id": batch_id,
                "agent_run_id": run_id,
                "thread_id": None,
                "po_line_id": str(po_line_id),
                "stage": stage,
                "status": status,
                "current_node": None,
                "pending_action_id": None,
                "updated_at": None,
            }

        self._hitl_state.apply_human_action(
            run_id=run_id,
            batch_id=batch_id,
            thread_id=checkpoint_thread_id,
            email_id=None,
            pending_action_id=resume_context["pending_action_id"],
            interrupt_type=resume_context["interrupt_type"],
            question=resume_context["question"],
            answer=resume_context["answer"],
            actor=resume_context["actor"],
            action_type=resume_context["action_type"],
            field_changes=resume_context.get("field_changes"),
            decision=resume_context.get("decision"),
            next_status=status,
            next_stage=stage,
            next_latest_snapshot={"po_line_id": str(po_line_id)},
            completed=True,
            po_line_id=po_line_id,
        )
        return self.get_stage(checkpoint_thread_id)

    def _require_material(self, sap_material_number: str, plant: str) -> None:
        if self._material_master.find(sap_material_number, plant) is None:
            raise ServiceError(
                "MATERIAL_NOT_FOUND",
                f"No material_master row for material={sap_material_number} plant={plant}.",
                status_code=422,
                details={"sap_material_number": sap_material_number, "plant": plant},
            )

    def _require_open_pending(self, thread_id: str, interrupt_type: str) -> dict[str, Any]:
        pending = self._pending_human_actions.get_open_for_thread(thread_id)
        if pending is None or pending["interrupt_type"] != interrupt_type:
            actual = None if pending is None else pending["interrupt_type"]
            raise self._thread_not_waiting(thread_id, interrupt_type, actual)
        return pending

    def _ensure_current(self, thread_id: str, expected_updated_at: str) -> dict[str, Any]:
        stage = self.get_stage(thread_id)
        if stage["updated_at"] != expected_updated_at:
            raise ServiceError(
                "THREAD_STALE",
                "Thread was updated by another reviewer. Refresh snapshot and retry.",
                status_code=409,
                details={"thread_id": thread_id, "latest_updated_at": stage["updated_at"]},
            )
        return stage

    @staticmethod
    def _thread_config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def _snapshot_state(state: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in state.items() if key != INTERRUPT_KEY}

    @staticmethod
    def _new_batch_id() -> str:
        return f"batch_po_{uuid4().hex[:12]}"

    @staticmethod
    def _new_checkpoint_thread_id() -> str:
        return f"thread_po_{uuid4().hex[:12]}"

    @staticmethod
    def _thread_not_found(thread_id: str) -> ServiceError:
        return ServiceError(
            "THREAD_NOT_FOUND",
            "Unknown thread_id.",
            status_code=404,
            details={"thread_id": thread_id},
        )

    @staticmethod
    def _resume_failed(thread_id: str, pending_action_id: UUID, exc: Exception) -> ServiceError:
        logger.exception(
            "Failed to resume PO validation thread %s from pending action %s",
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
    def _thread_not_waiting(thread_id: str, expected: str, actual: str | None) -> ServiceError:
        return ServiceError(
            "THREAD_NOT_WAITING",
            "Resume API called while thread is not paused for that action.",
            status_code=409,
            details={"thread_id": thread_id, "expected": expected, "actual": actual},
        )
