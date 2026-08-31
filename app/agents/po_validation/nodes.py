from __future__ import annotations

import functools
from typing import Literal

from langgraph.types import interrupt

from app.agents.po_validation.state import POGraphState
from app.repositories.cmir.cmir_record import CmirRecordRepository
from app.repositories.common.master_data import MasterDataRepository
from app.repositories.common.purchase_order import PurchaseOrderRepository
from app.repositories.process.workflow import ProcessingErrorRepository


def _capture_errors(error_type: str):
    """Turn an exception raised by a fallible node into a state["error"] value.

    This is what lets `handle_error` be one node reached from several places
    (PRD §6.2) instead of a copy-pasted try/except in every routing function.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapped(self, state: POGraphState) -> POGraphState:
            try:
                return fn(self, state)
            except Exception as exc:  # noqa: BLE001 - convert to routed state, not a raised exception
                return {
                    "error": {
                        "error_type": error_type,
                        "error_code": type(exc).__name__,
                        "error_message": str(exc),
                        "node_name": fn.__name__,
                    }
                }

        return wrapped

    return decorator


class PoValidationNodes:
    """LangGraph node functions for the PO Validation Agent.

    Mirrors app/agents/cmir/nodes.py: every collaborator is injected, and nodes
    only touch their own domain data (common.purchase_order_line,
    common.material_master, cmir.cmir_record, process.processing_error).
    process.agent_run / process.workflow_thread / process.human_action
    transitions are handled by PoValidationService after each graph.invoke(),
    exactly like CmirRunService._handle_graph_state.
    """

    def __init__(
        self,
        *,
        purchase_order_repository: PurchaseOrderRepository,
        master_data_repository: MasterDataRepository,
        cmir_repository: CmirRecordRepository,
        processing_error_repository: ProcessingErrorRepository,
    ) -> None:
        self._purchase_order_repository = purchase_order_repository
        self._master_data_repository = master_data_repository
        self._cmir_repository = cmir_repository
        self._processing_error_repository = processing_error_repository

    # ---- validation steps ---- #

    @_capture_errors("SYSTEM_ERROR")
    def persist_po_line(self, state: POGraphState) -> POGraphState:
        self._purchase_order_repository.update_line_status(state["po_line_id"], "VALIDATING")
        return {}

    @_capture_errors("LOOKUP_FAILURE")
    def validate_against_cmir(self, state: POGraphState) -> POGraphState:
        po_line = state["po_line"]
        match = self._cmir_repository.find_latest_for_customer_material(
            po_line["customer_id"], po_line["customer_material_code"]
        )
        if match is None:
            return {"sap_material_number": None, "cmir_match_found": False}
        return {"sap_material_number": match["material_identity"], "cmir_match_found": True}

    @_capture_errors("LOOKUP_FAILURE")
    def check_material_master(self, state: POGraphState) -> POGraphState:
        po_line = state["po_line"]
        sap_material_number = state["sap_material_number"]
        if sap_material_number is None:
            raise LookupError("check_material_master reached with no sap_material_number recorded")
        plant_id = po_line["plant_id"]
        material = self._master_data_repository.find_material_master(sap_material_number, plant_id)
        if material is None:
            raise LookupError(
                f"material_master has no row for material={sap_material_number} plant_id={plant_id}"
            )
        sufficient = material["available_quantity"] >= po_line["order_quantity"]
        return {
            "material": {
                "sap_material_number": material["sap_material_number"],
                "plant_id": material["plant_id"],
                "available_quantity": material["available_quantity"],
                "follow_up_material_id": material["follow_up_material_id"],
            },
            "quantity_sufficient": sufficient,
        }

    # ---- human-in-the-loop steps ---- #

    def human_manual_cmir_entry(self, state: POGraphState) -> POGraphState:
        po_line = state["po_line"]
        answer = interrupt(
            {
                "reason": "manual_cmir_entry",
                "po_line_id": state["po_line_id"],
                "po_number": po_line["po_number"],
                "po_line_number": po_line["po_line_number"],
                "customer_id": po_line["customer_id"],
                "customer_material_code": po_line["customer_material_code"],
            }
        )
        return {
            "sap_material_number": answer["sap_material_number"],
            "manual_entry_description": answer.get("description", ""),
        }

    def human_qty_mismatch_decision(self, state: POGraphState) -> POGraphState:
        material = state["material"]
        po_line = state["po_line"]
        # material_master.follow_up_material_id is a FK to common.material.id (a
        # plant-agnostic material identity), not a per-plant SAP material number --
        # a real restructure, not a rename (see app.models.common.material.MaterialMaster).
        # Resolving it to a substitute sap_material_number at this same plant would need
        # a "find_material_master by (material_id, plant_id)" repository method that
        # doesn't exist yet (MasterDataRepository only looks up by sap_material_number) --
        # out of scope for this repair (repositories are read-only this phase). Surfaced
        # as-is (stringified) under the same "suggested_substitute_material_code" key
        # PoValidationService.submit_qty_mismatch_decision already reads, so the
        # use_substitute path still round-trips end to end; flagged as an open gap.
        follow_up_material_id = material.get("follow_up_material_id")
        answer = interrupt(
            {
                "reason": "qty_mismatch_decision",
                "po_line_id": state["po_line_id"],
                "candidate": {
                    "sap_material_number": material["sap_material_number"],
                    "plant_id": material["plant_id"],
                    "available_quantity": material["available_quantity"],
                    "shortfall": po_line["order_quantity"] - material["available_quantity"],
                    "suggested_substitute_material_code": (
                        str(follow_up_material_id) if follow_up_material_id else None
                    ),
                },
            }
        )
        decision = answer["decision"]
        result: POGraphState = {"decision": decision}
        if decision == "use_substitute":
            substitute = answer.get("substitute_material_code") or (
                str(follow_up_material_id) if follow_up_material_id else None
            )
            result["sap_material_number"] = substitute
        return result

    # ---- outcome steps ---- #

    @_capture_errors("SYSTEM_ERROR")
    def create_cmir_record(self, state: POGraphState) -> POGraphState:
        po_line = state["po_line"]
        sap_material_number = state["sap_material_number"]
        if sap_material_number is None:
            raise ValueError("create_cmir_record reached with no sap_material_number recorded")
        self._cmir_repository.create_manual_mapping(
            customer_identity=po_line["customer_id"],
            material_identity=sap_material_number,
            target_customer_material_ref=po_line["customer_material_code"],
            description=state.get("manual_entry_description", ""),
        )
        return {}

    def mark_ready_for_so_creation(self, state: POGraphState) -> POGraphState:
        self._purchase_order_repository.update_line_status(state["po_line_id"], "READY_FOR_SO_CREATION")
        return {}

    def mark_ready_for_so_creation_partial(self, state: POGraphState) -> POGraphState:
        self._purchase_order_repository.update_line_status(
            state["po_line_id"], "READY_FOR_SO_CREATION_PARTIAL"
        )
        return {}

    def mark_discontinued(self, state: POGraphState) -> POGraphState:
        self._purchase_order_repository.update_line_status(state["po_line_id"], "DISCONTINUED")
        return {}

    def handle_error(self, state: POGraphState) -> POGraphState:
        error = state.get("error") or {}
        # process.processing_error (generalizing the old po_line_errors) has no
        # purchase_order_line_id column -- only job_item_id/agent_run_id (see
        # app.repositories.process.workflow.ProcessingErrorRepository and
        # PoValidationService's own docstring, point 3, for the same documented gap).
        # agent_run_id is the only thread this error row can be found by later.
        self._processing_error_repository.log(
            error.get("error_type", "SYSTEM_ERROR"),
            agent_run_id=state.get("run_id"),
            error_code=error.get("error_code"),
            error_message=error.get("error_message"),
            node_name=error.get("node_name", "unknown"),
            raw_error_detail=error,
        )
        self._purchase_order_repository.update_line_status(state["po_line_id"], "FAILED")
        return {}

    # ---- routing functions ---- #

    def route_after_persist(self, state: POGraphState) -> Literal["error", "continue"]:
        return "error" if state.get("error") else "continue"

    def route_after_cmir_validation(self, state: POGraphState) -> Literal["error", "found", "not_found"]:
        if state.get("error"):
            return "error"
        return "found" if state.get("cmir_match_found") else "not_found"

    def route_after_material_check(
        self, state: POGraphState
    ) -> Literal["error", "sufficient", "insufficient"]:
        if state.get("error"):
            return "error"
        return "sufficient" if state.get("quantity_sufficient") else "insufficient"

    def route_after_create_cmir_record(self, state: POGraphState) -> Literal["error", "continue"]:
        return "error" if state.get("error") else "continue"

    def route_after_qty_mismatch(
        self, state: POGraphState
    ) -> Literal["use_substitute", "proceed_anyway", "mark_stale"]:
        decision = state["decision"]
        if decision is None:
            raise ValueError("route_after_qty_mismatch reached with no decision recorded")
        return decision
