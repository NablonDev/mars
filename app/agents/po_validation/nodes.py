"""LangGraph node implementations for the PO validation workflow.

Implements discrete steps in the CMIR lookup, material-master availability
check, human-decision, and status-transition pipeline. Each method is a
single, atomic workflow node that reads from and updates the shared
POGraphState.
"""

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
        """Bind error_type to fn, returning the wrapped node that catches its exceptions."""

        @functools.wraps(fn)
        def wrapped(self, state: POGraphState) -> POGraphState:
            """Run fn and convert any exception it raises into a state["error"] value."""
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
    """LangGraph node functions for the PO Validation Agent, with collaborators injected.

    Nodes touch only their own domain data: purchase_order_line, material_master,
    cmir.cmir_record and process.processing_error. The process.agent_run,
    workflow_thread and human_action transitions belong to PoValidationService,
    which runs them after each graph.invoke().
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
        """Mark the line VALIDATING; a failure here is a SYSTEM_ERROR, not a lookup failure."""
        self._purchase_order_repository.update_line_status(state["po_line_id"], "VALIDATING")
        return {}

    @_capture_errors("LOOKUP_FAILURE")
    def validate_against_cmir(self, state: POGraphState) -> POGraphState:
        """Look up an existing CMIR mapping for this PO line's customer and material.

        Writes sap_material_number and cmir_match_found to state so
        route_after_cmir_validation can send the graph to check_material_master
        when a mapping was found, or to human_manual_cmir_entry when it wasn't.
        """
        po_line = state["po_line"]
        match = self._cmir_repository.find_latest_for_customer_material(
            po_line["customer_id"], po_line["customer_material_code"]
        )
        if match is None:
            return {"sap_material_number": None, "cmir_match_found": False}
        return {"sap_material_number": match["material_identity"], "cmir_match_found": True}

    @_capture_errors("LOOKUP_FAILURE")
    def check_material_master(self, state: POGraphState) -> POGraphState:
        """Check whether the resolved SAP material has enough available quantity at the plant.

        Requires sap_material_number to already be set (by validate_against_cmir or
        create_cmir_record); raises LookupError if it's missing or if
        common.material_master has no matching row, which _capture_errors turns
        into a routed LOOKUP_FAILURE. Writes material and quantity_sufficient to
        state for route_after_material_check.
        """
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
        """Interrupt to collect a manual CMIR mapping from a human operator.

        Reached when validate_against_cmir found no existing mapping. The resume value
        supplies sap_material_number and an optional description for create_cmir_record.
        """
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
        """Interrupt to collect a human decision on an available-quantity shortfall.

        Reached when check_material_master found insufficient available_quantity. The
        resume value supplies "use_substitute", "proceed_anyway" or "mark_stale" for
        route_after_qty_mismatch, and replaces sap_material_number for a substitute.
        """
        material = state["material"]
        po_line = state["po_line"]
        # follow_up_material_id is a FK to common.material.id, a plant-agnostic identity
        # rather than a per-plant SAP material number, and MasterDataRepository can only
        # look up by sap_material_number. It is surfaced stringified so the use_substitute
        # path still round-trips; resolving it at this plant remains an open gap.
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
        """Persist the operator-supplied manual CMIR mapping from human_manual_cmir_entry.

        Requires sap_material_number; raises ValueError otherwise, which _capture_errors
        turns into a routed SYSTEM_ERROR. The graph then re-runs check_material_master.
        """
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
        """Mark the PO line READY_FOR_SO_CREATION, a terminal node for sufficient stock."""
        self._purchase_order_repository.update_line_status(state["po_line_id"], "READY_FOR_SO_CREATION")
        return {}

    def mark_ready_for_so_creation_partial(self, state: POGraphState) -> POGraphState:
        """Mark the PO line READY_FOR_SO_CREATION_PARTIAL after a proceed_anyway decision."""
        self._purchase_order_repository.update_line_status(
            state["po_line_id"], "READY_FOR_SO_CREATION_PARTIAL"
        )
        return {}

    def mark_discontinued(self, state: POGraphState) -> POGraphState:
        """Mark the PO line DISCONTINUED after a mark_stale decision."""
        self._purchase_order_repository.update_line_status(state["po_line_id"], "DISCONTINUED")
        return {}

    def handle_error(self, state: POGraphState) -> POGraphState:
        """Log a captured error to process.processing_error and mark the PO line FAILED.

        A terminal node reached from any routing function that found state["error"] set;
        it reads only the error dict, never which node produced it.
        """
        error = state.get("error") or {}
        self._processing_error_repository.log(
            error.get("error_type", "SYSTEM_ERROR"),
            agent_run_id=state.get("run_id"),
            purchase_order_line_id=state["po_line_id"],
            error_code=error.get("error_code"),
            error_message=error.get("error_message"),
            node_name=error.get("node_name", "unknown"),
            raw_error_detail=error,
        )
        self._purchase_order_repository.update_line_status(state["po_line_id"], "FAILED")
        return {}

    # ---- routing functions ---- #

    def route_after_persist(self, state: POGraphState) -> Literal["error", "continue"]:
        """Route a captured persist_po_line error to handle_error, else to CMIR validation."""
        return "error" if state.get("error") else "continue"

    def route_after_cmir_validation(self, state: POGraphState) -> Literal["error", "found", "not_found"]:
        """Route a matched mapping to check_material_master, an unmatched one to a human."""
        if state.get("error"):
            return "error"
        return "found" if state.get("cmir_match_found") else "not_found"

    def route_after_material_check(
        self, state: POGraphState
    ) -> Literal["error", "sufficient", "insufficient"]:
        """Route sufficient stock to the ready node, a shortfall to a human decision."""
        if state.get("error"):
            return "error"
        return "sufficient" if state.get("quantity_sufficient") else "insufficient"

    def route_after_create_cmir_record(self, state: POGraphState) -> Literal["error", "continue"]:
        """Route a captured create_cmir_record error to handle_error, else to a re-check."""
        return "error" if state.get("error") else "continue"

    def route_after_qty_mismatch(
        self, state: POGraphState
    ) -> Literal["use_substitute", "proceed_anyway", "mark_stale"]:
        """Route to the outcome node matching the operator's quantity-mismatch decision.

        Raises ValueError on a missing decision: routing functions sit outside
        _capture_errors, so this guards a programming error, not a recoverable state.
        """
        decision = state["decision"]
        if decision is None:
            raise ValueError("route_after_qty_mismatch reached with no decision recorded")
        return decision
