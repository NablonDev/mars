"""API endpoints for the shared `process.workflow_thread` resource -- used by
both the `cmir` and `po_validation` domains, neither of which owns it solely.

`missing-fields`/`draft` stay CMIR-only in substance (`PoValidationService`
has no equivalent resume path -- its two interrupts are `qty_mismatch_decision`
and `manual_cmir_entry`, both routed through `decisions` below); calling
either against a po_validation-domain thread naturally 409s via
`THREAD_NOT_WAITING` rather than needing a domain check here, since that
thread's `status` can never be `waiting_missing_fields`/`waiting_approval`.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_po_service, get_service, parse_include
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import NotFoundError
from app.schemas.cmir.threads import (
    CmirApprovalDecisionRequest,
    CmirThreadSnapshotResponse,
    ManualCmirEntryDecisionRequest,
    QtyMismatchDecisionRequest,
    WorkflowThreadDecisionRequest,
    WorkflowThreadDetailResponse,
    WorkflowThreadDraftResponse,
    WorkflowThreadFieldsRequest,
    WorkflowThreadListResponse,
    WorkflowThreadResponse,
)
from app.schemas.po_validation.threads import PoValidationThreadSnapshotResponse
from app.services.cmir.run_service import CmirRunService
from app.services.po_validation.service import PoValidationService

router = APIRouter(tags=["workflow-threads"])

_INCLUDE_SNAPSHOT = parse_include(frozenset({"snapshot"}))


@router.get("/workflow-threads", response_model=Envelope[WorkflowThreadListResponse])
def list_workflow_threads(
    domain: Literal["cmir", "po_validation"] | None = Query(default=None),
    status: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    run_service: CmirRunService = Depends(get_service),
) -> Envelope[WorkflowThreadListResponse]:
    """Replaces `GET /runs?view=threads` -- filtered by `domain` instead of
    a domain-specific route.

    `WorkflowThreadRepository.list_threads` has no `domain` column to filter
    on at the query level (a thread's domain is derived, not stored --
    `email_event_id` set means `cmir`, `purchase_order_line_id` set means
    `po_validation`, see `WorkflowThreadSubject`'s two-nullable-FK pair), so
    this filters the already-paginated page in Python. Flagged, not silently
    smoothed over: a page can come back with fewer than `limit` items when
    the two domains' threads interleave -- `repositories/` is read-only this
    phase, so no `domain`-aware repository query was added.
    """
    result = run_service.list_runs(view="threads", status=status, stage=stage, limit=limit, cursor=cursor)
    items = result["items"]
    if domain == "cmir":
        items = [item for item in items if item["email_event_id"] is not None]
    elif domain == "po_validation":
        items = [item for item in items if item["purchase_order_line_id"] is not None]
    return success_envelope(
        WorkflowThreadListResponse(
            items=[WorkflowThreadResponse.model_validate(item) for item in items],
            next_cursor=result["next_cursor"],
        )
    )


@router.get(
    "/workflow-threads/{thread_id}",
    response_model=Envelope[WorkflowThreadDetailResponse],
)
def get_workflow_thread(
    thread_id: UUID,
    run_service: CmirRunService = Depends(get_service),
    po_run_service: PoValidationService = Depends(get_po_service),
    include: set[str] = Depends(_INCLUDE_SNAPSHOT),
) -> Envelope[WorkflowThreadDetailResponse]:
    """Stage is always returned; snapshot is opt-in via `?include=snapshot`
    (same pattern as the `penalties` routes' `?include=summary`) -- pure
    read, never schedules generation.

    `stage` itself is genuinely domain-agnostic (both services' `get_stage`
    delegate to the same shared repository method), so a single call resolves
    it and also serves as the resource's existence check. The snapshot,
    when requested, is domain-specific: `PoValidationService.get_snapshot`
    is tried first because it correctly rejects a CMIR-domain thread
    (`purchase_order_line_id` is `None` there) with `THREAD_NOT_FOUND`,
    whereas `CmirRunService.get_snapshot` is domain-agnostic-permissive at
    the repository level and would not reject a po_validation-domain thread
    -- see `app/api/v1/cmir.py`'s pre-restructure equivalent route for the
    same ordering rationale.
    """
    stage = run_service.get_stage(thread_id)

    snapshot: dict | None = None
    if "snapshot" in include:
        try:
            po_snapshot = po_run_service.get_snapshot(thread_id)
            snapshot = PoValidationThreadSnapshotResponse.model_validate(po_snapshot).model_dump(mode="json")
        except NotFoundError as exc:
            if exc.code != "THREAD_NOT_FOUND":
                raise
            cmir_snapshot = run_service.get_snapshot(thread_id)
            snapshot = CmirThreadSnapshotResponse.model_validate(cmir_snapshot).model_dump(mode="json")

    return success_envelope(WorkflowThreadDetailResponse(**stage, snapshot=snapshot))


@router.post(
    "/workflow-threads/{thread_id}/missing-fields",
    response_model=Envelope[WorkflowThreadResponse],
)
def submit_workflow_thread_missing_fields(
    thread_id: UUID,
    body: WorkflowThreadFieldsRequest,
    run_service: CmirRunService = Depends(get_service),
) -> Envelope[WorkflowThreadResponse]:
    result = run_service.submit_missing_fields(
        thread_id,
        actor=body.actor,
        fields=body.fields,
        expected_updated_at=body.expected_updated_at,
    )
    return success_envelope(WorkflowThreadResponse.model_validate(result))


@router.patch(
    "/workflow-threads/{thread_id}/draft",
    response_model=Envelope[WorkflowThreadDraftResponse],
)
def update_workflow_thread_draft(
    thread_id: UUID,
    body: WorkflowThreadFieldsRequest,
    run_service: CmirRunService = Depends(get_service),
) -> Envelope[WorkflowThreadDraftResponse]:
    """A `PATCH` on the in-flight review draft sub-resource."""
    result = run_service.update_draft(
        thread_id,
        actor=body.actor,
        fields=body.fields,
        expected_updated_at=body.expected_updated_at,
    )
    return success_envelope(WorkflowThreadDraftResponse.model_validate(result), message="Draft saved.")


@router.post(
    "/workflow-threads/{thread_id}/decisions",
    response_model=Envelope[WorkflowThreadResponse],
)
def submit_workflow_thread_decision(
    thread_id: UUID,
    body: WorkflowThreadDecisionRequest,
    run_service: CmirRunService = Depends(get_service),
    po_run_service: PoValidationService = Depends(get_po_service),
) -> Envelope[WorkflowThreadResponse]:
    """One generic decision-recording endpoint, `decision_type`-discriminated,
    covering CMIR approval decisions and both `po_validation` decision
    types -- a direct consequence of `workflow_thread` being a shared
    `process`-schema resource rather than owned by either domain."""
    if isinstance(body, CmirApprovalDecisionRequest):
        result = run_service.submit_decision(
            thread_id,
            actor=body.actor,
            decision=body.decision,
            expected_updated_at=body.expected_updated_at,
            reason=body.reason,
        )
    elif isinstance(body, QtyMismatchDecisionRequest):
        result = po_run_service.submit_qty_mismatch_decision(
            thread_id,
            actor=body.actor,
            decision=body.decision,
            substitute_material_code=body.substitute_material_code,
            expected_updated_at=body.expected_updated_at,
        )
    else:
        assert isinstance(body, ManualCmirEntryDecisionRequest)
        result = po_run_service.submit_manual_cmir_entry(
            thread_id,
            actor=body.actor,
            sap_material_number=body.sap_material_number,
            description=body.description,
            expected_updated_at=body.expected_updated_at,
        )
    return success_envelope(WorkflowThreadResponse.model_validate(result))
