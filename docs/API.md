# API

All endpoints are mounted under `/api/v1`. Interactive docs (Swagger UI)
are served at `/docs` when the app is running (`uvicorn app.main:app`).
Full request/response schemas: `app/schemas/`. Router implementations:
`app/api/v1/`.

No authentication on any endpoint yet -- internal/demo system, see
`docs/legacy/root-CLAUDE.md` "Do NOT" and `docs/FINE_ENGINE.md` "Open items."

## Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Readiness check, including DB connectivity |

`200` with `{"status": "ok", "database": "ok"}` when a `SELECT 1` over a
short-lived pooled connection succeeds; `503` with
`{"status": "degraded", "database": "unreachable"}` when it doesn't. No
driver or connection detail is ever returned -- it's logged instead.

## CMIR / PO Validation

See `docs/prd.md` for the business rules behind these endpoints and the
README's Identity Rule before touching reviewer/queue code -- `thread_id`
is the only identifier reviewer/UI actions may key on.

### Error contract

CMIR/PO-validation routes raise `app.core.exceptions.ServiceError` (a
separate exception type from the `AppError` hierarchy the Projected Fines
section below uses -- see `DATABASE.md`/`app/core/exceptions.py` for why --
but rendered by the same `register_exception_handlers` entry point in
`app/main.py`):

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
| `THREAD_STALE` | 409 | `expected_updated_at` didn't match current state -- another reviewer updated it first; refetch and retry |
| `WORKFLOW_RESUME_FAILED` | 500 | Unexpected failure resuming the LangGraph run; the pending action is left open, not silently closed |
| `CMIR_VERSION_CONFLICT` | 409 | Another approval superseded the active `cmir_records` row for this customer/material while this thread was waiting for a decision |
| `MATERIAL_NOT_FOUND` | 422 | PO Validation: a reviewer-submitted/chosen SAP material number has no `material_master` row |
| `QUEUE_NOT_CONFIGURED` | 500 | Email queue ingestion attempted without an email repository configured |

### CMIR Resolution Agent

Routes defined in `app/api/v1/cmir.py`, backed by `app.services.cmir_run_service.CMIRRunService`.

#### `POST /api/v1/ingest/emails`

Starts one email ingest batch: fetches unread Gmail messages and persists them as
queueable rows (`email_events`, `queue_status='new'`). Does **not** run the LangGraph
workflow synchronously -- actual extraction happens later via the Azure Function
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
  "threads": [ { "batch_id": "...", "agent_run_id": "0198f2b1-...", "thread_id": "", "email_id": "...", "stage": "NEW", "status": "new", ... } ]
}
```

`source` must be `"gmail"` (the only configured source today) or the route returns
`422 VALIDATION_ERROR`.

#### `GET /api/v1/runs`

Reviewer queue / ops views. `view` selects the shape:

| `view` | Returns |
|---|---|
| `threads` (default) | One row per reviewer-facing `workflow_threads` row |
| `agents` | One row per `agent_runs` row (per-email/per-PO-line execution) |
| `batches` | Roll-up per `batch_id` (total/waiting/completed/failed counts) |

Query params: `batch_id`, `agent_run_id` (UUID), `status`, `stage`, `sender`, `limit`
(default 50, max 200), `cursor` (opaque, from `next_cursor` in the previous page).

An unrecognized `view` returns `422 VALIDATION_ERROR`.

#### `POST /api/v1/internal/process-email`

**Internal only** -- called by `app/workers/service_bus_consumer.py`, not by reviewer
UIs. Runs one Service-Bus-delivered email through the LangGraph workflow synchronously.
Idempotent against `email_events.queue_status`: already-`processed` or in-flight emails
short-circuit to their current stage instead of re-running the graph.

#### `GET /api/v1/threads/{thread_id}/stage`

Current reviewer-facing stage for one thread -- `404 THREAD_NOT_FOUND` if unknown.

#### `GET /api/v1/threads/{thread_id}/snapshot`

Full review snapshot: email/PO-line context, current CMIR draft, diff against the
active record, and reviewer action history. **No fixed `response_model`** -- CMIR and
PO Validation snapshots have different shapes (see `PoThreadSnapshotResponse` vs
`ThreadSnapshotResponse` in `app/schemas/`). The handler tries the PO Validation
service first, falling back to the CMIR service only on `THREAD_NOT_FOUND`, so one
route transparently serves threads from either agent.

#### `POST /api/v1/threads/{thread_id}/missing-fields`

Resumes a thread paused at `AWAITING_MISSING_FIELDS` (`collect_missing_fields`
interrupt). Body: `{"actor": "...", "fields": {...}, "expected_updated_at": "..."}`.
`fields` keys must be in `CMIR_CONTENT_FIELDS` (`app/schemas/cmir.py`) or `422
VALIDATION_ERROR`.

#### `POST /api/v1/threads/{thread_id}/update`

Saves reviewer edits to the draft **without** resuming the graph -- the thread stays at
`AWAITING_APPROVAL`. Re-runs `merge_with_active` (`app/services/cmir_merge.py`) against
a freshly-fetched active `cmir_records` row on every call, so the diff and version
token shown next always reflect current reality, not a stale snapshot.

#### `POST /api/v1/threads/{thread_id}/decision`

Approves or rejects a thread paused at `AWAITING_APPROVAL`. Body: `{"actor": "...",
"decision": "approve"|"reject", "reason": "...", "expected_updated_at": "..."}`.
`reason` is required when rejecting. Approving can return `409 CMIR_VERSION_CONFLICT`
if another thread's approval already superseded the active record for the same
customer/material while this one was pending -- the thread still closes out (to
`COMPLETED_CONFLICT`), it does not silently retry.

### PO Validation Agent

Routes defined in `app/api/v1/po_validation.py`, backed by
`app.services.po_validation_service.PoValidationService`. Shares `agent_runs`,
`workflow_threads`, `pending_human_actions`, `hitl_actions`, and `cmir_records` with
the CMIR agent (see `DATABASE.md`).

#### `POST /api/v1/ingest/po-lines`

Persists each PO line and runs it through its own LangGraph workflow **synchronously,
inline** (no queue hop, unlike the CMIR email flow). Body: `{"lines": [{"po_number":
"...", "po_line_number": "...", "customer_id": "...", "customer_material_code": "...",
"plant": "...", "order_quantity": 100, "uom": "EA", "requested_delivery_date":
"2026-09-01"}]}`.

A line that resolves automatically (CMIR match found, sufficient material quantity)
never gets a reviewer-facing `thread_id` -- `thread_id` is `null` in the response for
that line, per the "no thread for the automatic path" rule.

#### `GET /api/v1/po-lines`

Lists PO lines, optionally filtered by `status` (`NEW`, `VALIDATING`,
`AWAITING_DECISION`, `READY_FOR_SO_CREATION`, `READY_FOR_SO_CREATION_PARTIAL`,
`DISCONTINUED`, `FAILED`). Paginated the same way as `/runs` (`limit`, `cursor`).

#### `GET /api/v1/po-lines/{po_line_id}/errors`

System/lookup failures logged against one PO line (`po_line_errors` table) -- distinct
from reviewer decisions, which live in `hitl_actions`. `422 VALIDATION_ERROR` for an
unknown `po_line_id`.

#### `POST /api/v1/threads/{thread_id}/qty-mismatch-decision`

Resumes a thread paused at `AWAITING_QTY_MISMATCH_DECISION`. Body: `{"actor": "...",
"decision": "use_substitute"|"proceed_anyway"|"mark_stale", "substitute_material_code":
"...", "expected_updated_at": "..."}`. `use_substitute` validates the (suggested or
explicitly chosen) material against `material_master` **before** touching the graph --
an unknown material returns `422 MATERIAL_NOT_FOUND` without ever invoking LangGraph.

#### `POST /api/v1/threads/{thread_id}/manual-cmir-entry`

Resumes a thread paused at `AWAITING_MANUAL_CMIR_ENTRY` (no automatic CMIR match was
found). Body: `{"actor": "...", "sap_material_number": "...", "description": "...",
"expected_updated_at": "..."}`. Same pre-graph material validation as above.

## Projected Fines

### Errors

Every deliberate error uses one envelope:

```json
{
  "error": {
    "code": "ORDER_NOT_FOUND",
    "message": "No order found with order_id='NOPE-999'"
  }
}
```

`code` comes from the raised `app/core/exceptions.py` class, `message` is
client-safe by construction. An unexpected exception is a `500` with a
fixed `{"error": {"code": "INTERNAL_ERROR", "message": "Internal server
error"}}` body -- no exception text, no stack trace. FastAPI's own
schema-validation rejections keep their native `{"detail": [...]}` `422`
shape.

Every response, including errors, carries an `X-Request-ID` header (echoed
from the request when supplied and well-formed, generated otherwise) --
quote it when reporting a failure, it's the key into the JSON logs.

### Master data

Thin create+list pairs over `MasterDataRepository`, no update/delete
(dimension data, rarely churns). Router: `app/api/v1/master_data.py`.

| Method | Path |
|---|---|
| POST / GET | `/retailers` |
| POST / GET | `/skus` |
| POST / GET | `/locations` |
| POST / GET | `/carriers` |

### Fine rules

Router: `app/api/v1/fine_rules.py`. `POST /fine-rules` accepts an
optional `tiers` array in the request body for `calc_type: "TIERED"`
rules (see `docs/FINE_ENGINE.md` "Pricing the fine").

| Method | Path |
|---|---|
| POST | `/fine-rules` |
| GET | `/fine-rules?retailer_id=` |

### Orders and facts

Router: `app/api/v1/orders.py` (order CRUD) and `app/api/v1/facts.py`
(the daily facts an ETL job/demo script writes). Every write here is
what `OrderRepository.build_snapshot` reads back for a projection run.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders` | Create an order header (409, `CONFLICT`/`ORDER_ALREADY_EXISTS`, if `order_id` exists) |
| GET | `/orders?order_status=` | List orders, optionally filtered |
| GET | `/orders/{order_id}` | Fetch one order |
| POST | `/orders/{order_id}/confirmations` | Record a SAP cut/ATP confirmation |
| POST | `/production-schedule` | Record a production-status fact (sku/location-keyed, not order-keyed) |
| PUT | `/orders/{order_id}/shipment` | Record a shipment/appointment fact (historized -- always an insert, never an overwrite) |
| POST | `/orders/{order_id}/demand-exceptions` | Flag a demand exception |
| POST | `/orders/{order_id}/actual-fines` | Record a post-delivery actual fine (for calibration) |
| GET | `/orders/{order_id}/actual-fines` | List actual fines for an order |

### Projections

Router: `app/api/v1/projections.py`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/projections/run` | Run one order (`order_id`) or every open order (`all_open: true`); optional `projection_date`, `stacking_mode_override` |
| GET | `/orders/{order_id}/projections` | Full dated history (the "trend") |
| GET | `/orders/{order_id}/exposure` | Latest day's total expected fine |

`POST /projections/run` returns `422` for `NoActiveRulesError` and `404`
for a missing order (`OrderNotFoundError`), both via the shared handler in
`app/core/exceptions.py`. `stacking_mode_override` is validated at the
schema level and only accepts `"SUM"` or `"MAX"` (`422` otherwise). Also
returns `500` (`INVALID_FINE_RULE_DATA`) if a stored fine rule's
`calc_type` doesn't match a known value -- a data-integrity failure in
`dim_fine_rule`, not a client input error.

### Fine Summaries

Router: `app/api/v1/fine_summaries.py`. LLM-powered (Azure OpenAI, see
`docs/RUNBOOK.md` "Azure OpenAI configuration") natural-language,
free-text summary of an order's current projected fine -- a plain prose
paragraph (see `app/agents/fine_summary_schema.py`), not a structured
multi-field breakdown. Generation is a background job
(`FastAPI.BackgroundTasks`), not something a client waits on inline -- a
real Azure OpenAI call for this feature takes ~45-90s+, too long to hold
an HTTP connection open for.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/summary` | Summarize the current projection for an order; body: optional `as_of_date` (defaults to today, UTC), optional `force_regenerate` (default `false`) |
| GET | `/orders/{order_id}/summary` | Poll a summary job; query param: optional `as_of_date` (same semantics as the POST) |

`POST` behavior:

- Validates fast and synchronously, no LLM call involved: order lookup
  (`404`), the order has projections (`422`), `as_of_date` in range
  (`422`) -- see below.
- **Cache hit** (not `force_regenerate`, and a `READY` row already exists
  for `(order_id, as_of_date, prompt_version)`): returns `200` with the
  full summary body immediately -- no job scheduled, nothing to poll.
  Body: `{order_id, as_of_date, prompt_version, model_name, summary}`,
  where `summary` is the model's free-text response.
- **Cache miss, or `force_regenerate: true`**: persists a `PENDING` row
  for that key (overwriting whatever was there before -- a stale `READY`
  summary or a previous `FAILED` attempt), schedules the actual LLM
  tool-calling loop via `BackgroundTasks`, and returns `202 Accepted`
  with `{order_id, as_of_date, prompt_version, status: "PENDING"}` so the
  caller knows what to poll. The LLM is never called inline with this
  request.
- Results are keyed on `(order_id, as_of_date, prompt_version)`: a
  prompt-version bump is a deliberate content change, so a summary
  generated under an old version is never served in place of one
  generated under the current one.
- `as_of_date` must fall within the order's real projection history --
  not before its earliest `fact_projected_fine` row, and not after
  today (UTC). This is enforced server-side (`InvalidAsOfDateError`,
  `422`), independent of `force_regenerate`, to prevent an unbounded
  number of distinct `(order_id, as_of_date)` cache keys -- and
  therefore unbounded real LLM calls -- from being minted by varying
  the date.
- `404` if `order_id` doesn't exist.
- `422` if the order has no projections yet (`NoProjectionExistsError`) --
  run `POST /projections/run` for it first.
- `422` if `as_of_date` is out of range for the order (`InvalidAsOfDateError`,
  see above).
- `500` (`INVALID_FINE_RULE_DATA`) if a stored fine rule's `calc_type`
  doesn't match a known value -- same data-integrity failure mode as
  `POST /projections/run`.

`GET` behavior (same `as_of_date` semantics/validation as the `POST`,
including the `404`/`422` cases above -- plus):

- Always `200` for a key that was validly `POST`ed at least once, body
  `{order_id, as_of_date, prompt_version, status, summary, error_message}`:
  - `status: "PENDING"` -- job scheduled, not finished yet;
    `summary`/`error_message` both `null`.
  - `status: "READY"` -- `summary` populated with the full body (same
    shape the `POST` cache-hit `200` returns); `error_message` `null`.
  - `status: "FAILED"` -- the Azure OpenAI call didn't produce a usable
    free-text summary within the bounded tool-calling loop
    (`ToolLoopExhaustedError`, upstream, not a client input error).
    `error_message` carries a generic client-safe message only; the
    underlying vendor error is logged server-side, never returned.
- `404` (`NoSummaryJobExistsError`) if no job was ever `POST`ed for
  this exact `(order_id, as_of_date, prompt_version)` key.

Known limitation: this endpoint has no request-level rate limiting --
see `docs/RUNBOOK.md` "Known limitation: no request-level rate
limiting".

### Run projection + summary together

Router: `app/api/v1/projections.py`. Composes the two features above in
the right order for whoever wants one call instead of two: runs the
projection synchronously, then -- only once it has succeeded -- schedules
the fine summary for that same projection date, exactly the sequencing
`POST /projections/run` followed by `POST /orders/{order_id}/summary`
would give if called by hand in order. Both of those endpoints are
unaffected and still work standalone; this is additive, not a
replacement.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/run` | Run one order's projection, then run (or reuse a cached) fine summary for the same day |

Body: `{projection_date?, stacking_mode_override?, force_regenerate_summary?}`
-- same fields as `POST /projections/run`'s single-order form, plus
`force_regenerate_summary` (default `false`), forwarded to the summary
step exactly as `POST /orders/{order_id}/summary`'s `force_regenerate`
would be.

Response body: `{projection: ProjectionResultResponse, summary: FineSummaryStatusResponse}`
(the same shapes `POST /projections/run` and `GET /orders/{order_id}/summary`
already return, nested together) --

- **`200`**: the summary was already cached (`READY`) for this order/day
  under the current prompt version -- `summary.summary` is populated,
  nothing scheduled.
- **`202`**: cache miss (or `force_regenerate_summary: true`) -- the
  summary job is scheduled via `BackgroundTasks` just like the standalone
  endpoint; `summary.status` is `"PENDING"`. Poll
  `GET /orders/{order_id}/summary` to get the result, same as the
  standalone flow.

Failure modes are the projection step's, since it runs first and gates
the summary step entirely -- `404`/`422`/`500` exactly as documented
above for `POST /projections/run`. If the projection fails, no summary
job is scheduled at all (no `PENDING` row is left behind to poll).

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/run \
  -H "Content-Type: application/json" -d '{}'
```

### Admin

Router: `app/api/v1/admin.py`. Both idempotent -- safe to call repeatedly
to reset a demo environment.

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/seed-master-data` | Seed retailers/SKUs/locations/carriers/rules + the 4 example orders |
| POST | `/admin/simulate-daily-run` | Replay all 4 worked-example scenarios day-by-day through the real service layer |
