# API Reference

HTTP API surface for the CMIR Resolution Agent and PO Validation Agent, both served by
one FastAPI process (`app/main.py`). Base path for every route: `/api/v1`. Route
definitions live in `app/api/v1/cmir.py` and `app/api/v1/po_validation.py`; both are
mounted via `app/api/router.py`.

See `docs/prd.md` for the business rules behind these endpoints and `CLAUDE.md`'s
Identity Model section before touching reviewer/queue code — `thread_id` is the only
identifier reviewer/UI actions may key on.

## Error contract

Every error response has the same shape, raised as `app.core.exceptions.ServiceError`
and rendered by a global exception handler in `app/main.py`:

```json
{
  "error": {
    "code": "THREAD_NOT_FOUND",
    "message": "Unknown thread_id.",
    "details": { "thread_id": "thread_01J4A" }
  }
}
```

| Code | HTTP status | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 422 | Bad request body/field (unknown field name, invalid `view`, missing decision reason, etc.) |
| `THREAD_NOT_FOUND` | 404 | Unknown `thread_id` |
| `THREAD_NOT_WAITING` | 409 | Resume-style API called while the thread isn't paused for that specific action |
| `THREAD_STALE` | 409 | `expected_updated_at` didn't match current state — another reviewer updated it first; refetch and retry |
| `WORKFLOW_RESUME_FAILED` | 500 | Unexpected failure resuming the LangGraph run; the pending action is left open, not silently closed |
| `CMIR_VERSION_CONFLICT` | 409 | Another approval superseded the active `cmir_records` row for this customer/material while this thread was waiting for a decision |
| `MATERIAL_NOT_FOUND` | 422 | PO Validation: a reviewer-submitted/chosen SAP material number has no `material_master` row |
| `QUEUE_NOT_CONFIGURED` | 500 | Email queue ingestion attempted without an email repository configured |

## CMIR Resolution Agent

Routes defined in `app/api/v1/cmir.py`, backed by `app.services.cmir_run_service.CMIRRunService`.

### `POST /api/v1/ingest/emails`

Starts one email ingest batch: fetches unread Gmail messages and persists them as
queueable rows (`email_events`, `queue_status='new'`). Does **not** run the LangGraph
workflow synchronously — actual extraction happens later via the Azure Function
timer trigger + Service Bus (see `RUNBOOK.md`).

Request:
```json
{
  "max_workers": 4,
  "source": "gmail",
  "filters": { "subject_contains": "CMIR", "unread_only": true }
}
```

Response `202`:
```json
{
  "batch_id": "batch_49cb72cee995",
  "status": "ready_for_queue",
  "total_threads": 1,
  "threads": [ { "batch_id": "...", "agent_run_id": 0, "thread_id": "", "email_id": "...", "stage": "NEW", "status": "new", ... } ]
}
```

`source` must be `"gmail"` (the only configured source today) or the route returns
`422 VALIDATION_ERROR`.

### `GET /api/v1/runs`

Reviewer queue / ops views. `view` selects the shape:

| `view` | Returns |
|---|---|
| `threads` (default) | One row per reviewer-facing `workflow_threads` row |
| `agents` | One row per `agent_runs` row (per-email/per-PO-line execution) |
| `batches` | Roll-up per `batch_id` (total/waiting/completed/failed counts) |

Query params: `batch_id`, `agent_run_id`, `status`, `stage`, `sender`, `limit` (default
50, max 200), `cursor` (opaque, from `next_cursor` in the previous page).

An unrecognized `view` returns `422 VALIDATION_ERROR`.

### `POST /api/v1/internal/process-email`

**Internal only** — called by `app/workers/service_bus_consumer.py`, not by reviewer
UIs. Runs one Service-Bus-delivered email through the LangGraph workflow synchronously.
Idempotent against `email_events.queue_status`: already-`processed` or in-flight emails
short-circuit to their current stage instead of re-running the graph.

### `GET /api/v1/threads/{thread_id}/stage`

Current reviewer-facing stage for one thread — `404 THREAD_NOT_FOUND` if unknown.

### `GET /api/v1/threads/{thread_id}/snapshot`

Full review snapshot: email/PO-line context, current CMIR draft, diff against the
active record, and reviewer action history. **No fixed `response_model`** — CMIR and
PO Validation snapshots have different shapes (see `PoThreadSnapshotResponse` vs
`ThreadSnapshotResponse` in `app/schemas/`). The handler tries the PO Validation
service first, falling back to the CMIR service only on `THREAD_NOT_FOUND`, so one
route transparently serves threads from either agent.

### `POST /api/v1/threads/{thread_id}/missing-fields`

Resumes a thread paused at `AWAITING_MISSING_FIELDS` (`collect_missing_fields`
interrupt). Body: `{"actor": "...", "fields": {...}, "expected_updated_at": "..."}`.
`fields` keys must be in `CMIR_CONTENT_FIELDS` (`app/schemas/cmir.py`) or `422
VALIDATION_ERROR`.

### `POST /api/v1/threads/{thread_id}/update`

Saves reviewer edits to the draft **without** resuming the graph — the thread stays at
`AWAITING_APPROVAL`. Re-runs `merge_with_active` (`app/services/cmir_merge.py`) against
a freshly-fetched active `cmir_records` row on every call, so the diff and version
token shown next always reflect current reality, not a stale snapshot.

### `POST /api/v1/threads/{thread_id}/decision`

Approves or rejects a thread paused at `AWAITING_APPROVAL`. Body: `{"actor": "...",
"decision": "approve"|"reject", "reason": "...", "expected_updated_at": "..."}`.
`reason` is required when rejecting. Approving can return `409 CMIR_VERSION_CONFLICT`
if another thread's approval already superseded the active record for the same
customer/material while this one was pending — the thread still closes out (to
`COMPLETED_CONFLICT`), it does not silently retry.

## PO Validation Agent

Routes defined in `app/api/v1/po_validation.py`, backed by
`app.services.po_validation_service.PoValidationService`. Shares `agent_runs`,
`workflow_threads`, `pending_human_actions`, `hitl_actions`, and `cmir_records` with
the CMIR agent (see `DATABASE.md`).

### `POST /api/v1/ingest/po-lines`

Persists each PO line and runs it through its own LangGraph workflow **synchronously,
inline** (no queue hop, unlike the CMIR email flow). Body: `{"lines": [{"po_number":
"...", "po_line_number": "...", "customer_id": "...", "customer_material_code": "...",
"plant": "...", "order_quantity": 100, "uom": "EA", "requested_delivery_date":
"2026-09-01"}]}`.

A line that resolves automatically (CMIR match found, sufficient material quantity)
never gets a reviewer-facing `thread_id` — `thread_id` is `null` in the response for
that line, per the "no thread for the automatic path" rule.

### `GET /api/v1/po-lines`

Lists PO lines, optionally filtered by `status` (`NEW`, `VALIDATING`,
`AWAITING_DECISION`, `READY_FOR_SO_CREATION`, `READY_FOR_SO_CREATION_PARTIAL`,
`DISCONTINUED`, `FAILED`). Paginated the same way as `/runs` (`limit`, `cursor`).

### `GET /api/v1/po-lines/{po_line_id}/errors`

System/lookup failures logged against one PO line (`po_line_errors` table) — distinct
from reviewer decisions, which live in `hitl_actions`. `422 VALIDATION_ERROR` for an
unknown `po_line_id`.

### `POST /api/v1/threads/{thread_id}/qty-mismatch-decision`

Resumes a thread paused at `AWAITING_QTY_MISMATCH_DECISION`. Body: `{"actor": "...",
"decision": "use_substitute"|"proceed_anyway"|"mark_stale", "substitute_material_code":
"...", "expected_updated_at": "..."}`. `use_substitute` validates the (suggested or
explicitly chosen) material against `material_master` **before** touching the graph —
an unknown material returns `422 MATERIAL_NOT_FOUND` without ever invoking LangGraph.

### `POST /api/v1/threads/{thread_id}/manual-cmir-entry`

Resumes a thread paused at `AWAITING_MANUAL_CMIR_ENTRY` (no automatic CMIR match was
found). Body: `{"actor": "...", "sap_material_number": "...", "description": "...",
"expected_updated_at": "..."}`. Same pre-graph material validation as above.
