# API

All endpoints are mounted under `/api/v1`. Interactive docs (Swagger UI)
are served at `/docs` when the app is running (`uvicorn app.main:app`) and
`DOCS_ENABLED=true` -- closed by default, see `docs/DEPLOYMENT.md`
"Configuration reference". Full request/response schemas: `app/schemas/`.
Router implementations: `app/api/v1/`.

## Authentication

Every route requires the `X-Internal-Api-Key` header, checked against the
`INTERNAL_API_KEY` environment variable (`app/api/dependencies.py::require_internal_api_key`,
wired in globally at the router-aggregation point in `app/api/router.py` --
new routers are covered automatically, nothing per-route to remember). The
one exception is `GET /health`, left open for load balancers/uptime
monitors. Missing or wrong key -> `401` with a generic
`{"detail": "Not authenticated"}` body; it never echoes what was sent or
what was expected.

```bash
curl http://127.0.0.1:8000/api/v1/orders -H "X-Internal-Api-Key: $INTERNAL_API_KEY"
```

Every curl example below omits this header for brevity -- add it to every
call except `/health`.

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

`details` is only present on a 4xx. On a 5xx it is withheld entirely: those
call sites carry internal failure text, which belongs in the log stream with
the request id, not in the response body.

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

**Internal only** -- called by `app/workers/cmir_service_bus_consumer.py`, not by reviewer
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
(dimension data, rarely churns). Router: `app/api/v1/fine_master_data.py`.

| Method | Path |
|---|---|
| POST / GET | `/retailers` |
| POST / GET | `/skus` |
| POST / GET | `/locations` |
| POST / GET | `/carriers` |

### Fine rules

Router: `app/api/v1/fine_rules.py`. `POST /fine-rules` accepts an
optional `tiers` array in the request body for `calc_type: "TIERED"`
rules.

| Method | Path |
|---|---|
| POST | `/fine-rules` |
| GET | `/fine-rules?retailer_id=` |

### Orders and facts

Router: `app/api/v1/orders.py` (order CRUD) and `app/api/v1/fine_projection/facts.py`
(the daily facts an ETL job/demo script writes). Every write here is
what `OrderRepository.build_snapshot` reads back for a projection run.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders` | Create an order header (409, `CONFLICT`/`ORDER_ALREADY_EXISTS`, if `order_id` exists) |
| GET | `/orders?order_status=` | List orders, optionally filtered |
| GET | `/orders/{order_id}` | Fetch one order |
| POST | `/orders/{order_id}/confirmations` | Record a SAP cut/ATP confirmation |
| POST | `/production-schedules` | Record a production-status fact (sku/location-keyed, not order-keyed) |
| POST | `/orders/{order_id}/shipments` | Record a shipment/appointment fact (historized -- always an insert, never an overwrite; append-only, no update/overwrite semantics) |
| POST | `/orders/{order_id}/demand-exceptions` | Flag a demand exception |
| POST | `/orders/{order_id}/actual-fines` | Record a post-delivery actual fine (for calibration) |
| GET | `/orders/{order_id}/actual-fines` | List actual fines for an order |

### PO delivery-change requests

Router: `app/api/v1/fine_projection/po_delivery_change_requests.py`. Vendor-initiated
requests asking a retailer for a later delivery date, and the retailer's
response to each -- the negotiation step ops takes before falling back to
fine mitigation.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/po-delivery-change-requests` | Create a request (`reason_code`: `SHORTAGE`/`DELAY`/`OTHER`, `proposed_delivery_date`, optional `notes`); `201` |
| POST | `/po-delivery-change-requests/{request_id}/response` | Record the retailer's decision (`decision`: `ACCEPTED`/`COUNTERED`/`REJECTED`; `COUNTERED` requires `countered_delivery_date`) |
| GET | `/orders/{order_id}/po-delivery-change-requests` | Full request/response history for an order |

`404` (`ORDER_NOT_FOUND`) for an unknown `order_id` on create/history, and
(`PO_DELIVERY_CHANGE_REQUEST_NOT_FOUND`) for an unknown `request_id` on
response. `409` (`ACTIVE_PO_DELIVERY_CHANGE_REQUEST_EXISTS`) if the order
already has a `PENDING` request -- only one active request per order at a
time. `422` for `PO_DELIVERY_CHANGE_LEAD_TIME_ERROR` (fewer days remain
before `current_required_ship_date` than the retailer's
`extension_min_lead_days` policy allows) and
`INVALID_PO_DELIVERY_CHANGE_RESPONSE` (responding to a request that isn't
`PENDING` any more, a missing/misplaced `countered_delivery_date`, or one
that falls outside `(baseline_delivery_date, proposed_delivery_date)`).
There's no shadow mitigation tracking here by design: while a request is
`PENDING`, the existing daily projection/mitigation cycle is itself the
fallback, and every terminal outcome (`ACCEPTED`/`COUNTERED`/`REJECTED`/
`EXPIRED`) re-triggers `FineProjectionService.run_for_order` for that order
immediately instead of waiting for the next batch.

### Projections

Router: `app/api/v1/fine_projection/projections.py`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/projections` | Run a projection for one order; optional `projection_date`, `stacking_mode_override` |
| POST | `/projections/runs` | Batch controller action: run every open order (`all_open: true` required); optional `projection_date`, `stacking_mode_override` |
| GET | `/orders/{order_id}/projections` | Full dated history (the "trend") |
| GET | `/orders/{order_id}/exposure` | Latest day's total expected fine |

`POST /projections/runs` now only accepts the `all_open: true` fan-out
case -- a lone `order_id` in the body is rejected with `422` and a message
pointing at `POST /orders/{order_id}/projections` instead (no backward
compatibility window; this is a deliberate split, not a deprecation).

Both routes return `422` for `NoActiveRulesError` and `404` for a missing
order (`OrderNotFoundError`), both via the shared handler in
`app/core/exceptions.py`. `stacking_mode_override` is validated at the
schema level and only accepts `"SUM"` or `"MAX"` (`422` otherwise). Also
returns `500` (`INVALID_FINE_RULE_DATA`) if a stored fine rule's
`calc_type` doesn't match a known value -- a data-integrity failure in
`fine_rule`, not a client input error.

### Fine Projection Summaries

Router: `app/api/v1/fine_projection/summaries.py`. LLM-powered (Azure OpenAI, see
`docs/RUNBOOK.md` "Azure OpenAI configuration") natural-language,
free-text summary of an order's current projected fine -- a plain prose
paragraph (see `app/agents/fine_projection/schema.py`), not a structured
multi-field breakdown. Generation is a background job
(`FastAPI.BackgroundTasks`), not something a client waits on inline -- a
real Azure OpenAI call for this feature takes ~45-90s+, too long to hold
an HTTP connection open for.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/projection-summary` | Summarize the current projection for an order; body: optional `as_of_date` (defaults to today, UTC), optional `force_regenerate` (default `false`) |
| GET | `/orders/{order_id}/projection-summary` | Poll a summary job; query param: optional `as_of_date` (same semantics as the POST) |

`POST` behavior:

- Validates fast and synchronously, no LLM call involved: order lookup
  (`404`), the order has projections (`422`), `as_of_date` in range
  (`422`) -- see below.
- **Cache hit** (not `force_regenerate`, and a `READY` row already exists
  for `(order_id, as_of_date, prompt_version)`): returns `200` with the
  full summary body immediately -- no job scheduled, nothing to poll.
  Body: `{order_id, as_of_date, prompt_version, model_name, summary,
  is_reused, generated_for_date, unchanged_since, unchanged_for_days}`,
  where `summary` is the model's free-text response and the last four
  fields are the reuse disclosure (see below).
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
  not before its earliest `projected_fine` row, and not after
  today (UTC). This is enforced server-side (`InvalidAsOfDateError`,
  `422`), independent of `force_regenerate`, to prevent an unbounded
  number of distinct `(order_id, as_of_date)` cache keys -- and
  therefore unbounded real LLM calls -- from being minted by varying
  the date.
- `404` if `order_id` doesn't exist.
- `422` if the order has no projections yet (`NoProjectionExistsError`) --
  run `POST /projections/runs` for it first.
- `422` if `as_of_date` is out of range for the order (`InvalidAsOfDateError`,
  see above).
- `500` (`INVALID_FINE_RULE_DATA`) if a stored fine rule's `calc_type`
  doesn't match a known value -- same data-integrity failure mode as
  `POST /projections/runs`.

**Reuse disclosure**: when `settings.summary_reuse_enabled` is `true`
(default `false`) and there's no exact-date cache hit, the service will
look for the nearest earlier `READY` summary for this order (within
`settings.summary_max_reuse_days`, default 7) whose *content
fingerprint* -- the violations/probabilities/statuses that actually
drive the narrative, not `as_of_date` itself or fields like
`days_to_delivery` -- is byte-for-byte identical to today's. If one is
found, its narrative text is copied forward under today's `as_of_date`
instead of making a new LLM call, and the response/job body carries:
  - `is_reused` (`bool`): `true` if this summary's text was copied
    forward from an earlier date rather than freshly generated for
    `as_of_date`; `false` otherwise.
  - `generated_for_date` (`date | null`): the date the narrative was
    actually generated for -- equal to `as_of_date` when `is_reused` is
    `false`, or the earlier source date when `is_reused` is `true`.
  - `unchanged_since` (`date | null`): `null` when `is_reused` is
    `false`; otherwise the earlier source date, i.e. the date since
    which the underlying content has stayed identical.
  - `unchanged_for_days` (`int | null`): `null` when `is_reused` is
    `false`; otherwise `(as_of_date - unchanged_since).days`.

`GET` behavior (same `as_of_date` semantics/validation as the `POST`,
including the `404`/`422` cases above -- plus):

- Always `200` for a key that was validly `POST`ed at least once, body
  `{order_id, as_of_date, prompt_version, status, summary, error_message}`
  where a non-null `summary` has the same shape as the `POST` cache-hit
  body above, including the four reuse-disclosure fields:
  - `status: "PENDING"` -- job scheduled, not finished yet;
    `summary`/`error_message` both `null`.
  - `status: "READY"` -- `summary` populated with the full body (same
    shape the `POST` cache-hit `200` returns); `error_message` `null`.
  - `status: "FAILED"` -- the Azure OpenAI call didn't produce a usable
    free-text summary within the bounded tool-calling loop
    (`ToolLoopExhaustedError`, upstream, not a client input error).
    `error_message` carries a generic client-safe message only; the
    underlying vendor error is logged server-side, never returned.
- `404` (`NoProjectionSummaryJobExistsError`) if no job was ever `POST`ed for
  this exact `(order_id, as_of_date, prompt_version)` key.

Known limitation: this endpoint has no request-level rate limiting --
see `docs/RUNBOOK.md` "Known limitation: no request-level rate
limiting".

### Run projection + summary together

Router: `app/api/v1/fine_projection/projections.py`. Composes the two features above in
the right order for whoever wants one call instead of two: runs the
projection synchronously, then -- only once it has succeeded -- schedules
the fine projection summary for that same projection date, exactly the sequencing
`POST /orders/{order_id}/projections` followed by
`POST /orders/{order_id}/projection-summary` would give if called by hand in order.
Both of those endpoints are unaffected and still work standalone; this is
additive, not a replacement.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/projections/runs` | Run one order's projection, then run (or reuse a cached) fine projection summary for the same day |

Body: `{projection_date?, stacking_mode_override?, force_regenerate_summary?}`
-- same fields as `POST /orders/{order_id}/projections`'s body, plus
`force_regenerate_summary` (default `false`), forwarded to the summary
step exactly as `POST /orders/{order_id}/projection-summary`'s `force_regenerate`
would be.

Response body: `{projection: ProjectionResultResponse, summary: FineProjectionSummaryStatusResponse}`
(the same shapes `POST /orders/{order_id}/projections` and
`GET /orders/{order_id}/projection-summary` already return, nested together) --

- **`200`**: the summary was already cached (`READY`) for this order/day
  under the current prompt version -- `summary.summary` is populated,
  nothing scheduled.
- **`202`**: cache miss (or `force_regenerate_summary: true`) -- the
  summary job is scheduled via `BackgroundTasks` just like the standalone
  endpoint; `summary.status` is `"PENDING"`. Poll
  `GET /orders/{order_id}/projection-summary` to get the result, same as the
  standalone flow.

Failure modes are the projection step's, since it runs first and gates
the summary step entirely -- `404`/`422`/`500` exactly as documented
above for `POST /orders/{order_id}/projections`. If the projection fails,
no summary job is scheduled at all (no `PENDING` row is left behind to
poll).

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/projections/runs \
  -H "Content-Type: application/json" -d '{}'
```

### Mitigation options

Router: `app/api/v1/fine_mitigation/mitigations.py`. Ranks candidate mitigation actions
(`ACCEPT`, `SPEED_UP_PRODUCTION`, `SPLIT_SHIPMENT`, `FASTER_CARRIER`)
against an order's already-persisted projection for one day -- see
`app/services/fine_mitigation/engine.py`. Computation is synchronous, no
LLM call, no job queue -- the same posture as `POST /orders/{order_id}/projections`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/mitigation-options` | Compute and persist ranked mitigation options for one order; optional `projection_date` (defaults to today, UTC) |
| GET | `/orders/{order_id}/mitigation-options` | Latest persisted, ranked options for an order |

`POST` returns `404` (`OrderNotFoundError`) for an unknown order, and
`422` (`NoProjectionExistsError`) if the order has no projection for the
requested `projection_date` yet -- run `POST /orders/{order_id}/projections`
for that date first; mitigation evaluates against an already-persisted
projection, it never recomputes one itself. `GET` returns `404` for an
unknown order and `422` (`NoMitigationOptionsExistError`) if nothing has
been computed yet.

### Fine Mitigation Summaries

Router: `app/api/v1/fine_mitigation/summaries.py`. Full mirror of the
Fine Projection Summaries contract above, for explaining an order's
ranked mitigation options (cost, saving, risk, confidence) in plain
language instead of a projection trace.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/mitigation-summary` | Summarize the current ranked mitigation options for an order; body: optional `as_of_date` (defaults to today, UTC), optional `force_regenerate` (default `false`) |
| GET | `/orders/{order_id}/mitigation-summary` | Poll a mitigation-summary job; query param: optional `as_of_date` (same semantics as the POST) |

Same cache-hit/cache-miss, `202`-then-poll, `force_regenerate`, and
`as_of_date`-bounds behavior as `POST`/`GET /orders/{order_id}/projection-summary`
-- substituting `NoMitigationOptionsExistError` (`422`) for
`NoProjectionExistsError` (run `POST /orders/{order_id}/mitigation-options`
first) and `NoMitigationSummaryJobExistsError` (`404`) for
`NoProjectionSummaryJobExistsError`. Keyed on `(order_id, as_of_date,
prompt_version)`, same reasoning as the projection-summary table.
Response bodies are the same shape too: `POST` cache-hit `200` and the
`summary` object inside `GET`'s `200` are both `{order_id, as_of_date,
prompt_version, model_name, summary, is_reused, generated_for_date,
unchanged_since, unchanged_for_days}` -- see "Reuse disclosure" under
Fine Projection Summaries above for what the last four fields mean;
the semantics (including `settings.summary_reuse_enabled` /
`summary_max_reuse_days`) are identical here. The only difference is
*what* the content fingerprint driving reuse-across-days hashes: here
it's the ranked mitigation options themselves
(action/cost/saving/risk/confidence), not projection-specific fields --
see `app/services/fine_mitigation/summary.py::_compute_content_fingerprint`.

### Run mitigation options + mitigation summary together

Router: `app/api/v1/fine_mitigation/mitigations.py`. Sibling of `POST /orders/{order_id}/projections/runs`
for the mitigation side, not a change to that endpoint's contract: that
response shape (`{projection, summary}`) has no slot for mitigation, so
this is a separate, explicit endpoint rather than an overload.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/mitigation-options/runs` | Compute mitigation options for one order, then run (or reuse a cached) mitigation summary for the same day |

Body: `{projection_date?, force_regenerate_summary?}`. Response body:
`{mitigation_options: MitigationOptionsResponse, summary: FineMitigationSummaryStatusResponse}`.
`200` when the summary was already cached; `202` on a cache miss (or
`force_regenerate_summary: true`), same polling contract as the standalone
mitigation-summary endpoint. Failure modes are the mitigation-options
step's, since it runs first and gates the summary step -- `404`/`422`
exactly as documented above for `POST /orders/{order_id}/mitigation-options`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/mitigation-options/runs \
  -H "Content-Type: application/json" -d '{}'
```

### Run everything together

Router: `app/api/v1/fine_runs.py`. Chains all four steps above for one
order in a single call: projection -> projection summary -> mitigation
options -> mitigation summary -- the same sequencing
`POST /orders/{order_id}/projections/runs` followed by `POST /orders/{order_id}/mitigation-options/runs`
would give if called by hand in order, collapsed into one request. Neither
of those two combo endpoints is affected and both still work standalone;
this is additive, not a replacement.

Its actual value over calling the two combo endpoints back-to-back
yourself: mitigation is ranked against `projection_result.projection_date`
-- the exact date this same call's projection step just computed --
never against `body.projection_date` or "whatever the latest projection
happens to be". That means `NO_PROJECTION_EXISTS` (`422`) can never
happen here, unlike `POST /orders/{order_id}/mitigation-options/runs`, which
requires the caller to have already run a projection for that date
separately.

| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/fine-runs` | Run one order's projection, projection summary, mitigation options, and mitigation summary, chained in one call |

Body: `{projection_date?, stacking_mode_override?, force_regenerate_projection_summary?, force_regenerate_mitigation_summary?}`
-- `projection_date`/`stacking_mode_override` are forwarded to the
projection step exactly as `POST /orders/{order_id}/projections`'s body
would; the two `force_regenerate_*` flags are genuinely independent
caches (projection-summary vs. mitigation-summary) forwarded to their
respective `get_or_schedule` calls -- reusing one flag for both would
force-regenerate a cache the caller never asked to touch.

Response body: `{projection: ProjectionResultResponse, projection_summary:
FineProjectionSummaryStatusResponse, mitigation_options:
MitigationOptionsResponse, mitigation_summary: FineMitigationSummaryStatusResponse}`
-- the same four shapes the standalone endpoints already return, nested
together.

- **`200`**: both summaries were already cached (`READY`) for this
  order/day -- both `summary` fields are populated, nothing scheduled.
- **`202`**: either summary was a cache miss (or its `force_regenerate_*`
  flag was set) -- that summary is scheduled via `BackgroundTasks` just
  like the standalone endpoints, with `status: "PENDING"`; the other
  summary can still come back `READY` inline in the same response if it
  was cached. Poll `GET /orders/{order_id}/projection-summary` and/or
  `GET /orders/{order_id}/mitigation-summary` for whichever is `PENDING`.

Failure modes are the projection step's for the whole chain -- `404`/`422`/`500`
exactly as documented above for `POST /orders/{order_id}/projections` --
since it runs first and gates every step after it. If the projection
fails, no mitigation is computed and no summary job is scheduled at all.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/fine-runs \
  -H "Content-Type: application/json" -d '{}'
```

### Batches

Router: `app/api/v1/batches.py`. Trigger and observe queue-backed batch
runs. Background on the queue itself: `docs/JOB-QUEUE-WALKTHROUGH.md`;
how to run and deploy it: `docs/DEPLOYMENT.md`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/batches/runs` | Enqueue one `ORDER_RUN` job per OPEN order |
| GET | `/batches/{job_run_id}` | Status counts and completion for one run |
| GET | `/batches/{job_run_id}/items` | Per-item detail, filterable and paged |

#### `POST /batches/runs`

**Enqueue-only. It never runs a projection or a summary inline.** Returns
`202` immediately.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/batches/runs \
  -H "Content-Type: application/json" \
  -d '{"projection_date": "2026-08-15", "stacking_mode_override": "MAX"}'
```

Both body fields are optional -- `projection_date` defaults to today (UTC),
`stacking_mode_override` to each retailer's configured policy.

```json
{
  "job_run_id": "0f3c…",
  "requested_item_count": 4,
  "dispatch_mode": "postgres",
  "execution_note": "Enqueued; will be processed by the next scheduled batch drain."
}
```

Read `execution_note` literally, because `202` means different things per
backend:

| `dispatch_mode` | What actually happens next |
|---|---|
| `postgres` | Rows are written. **Nothing runs them** until a drain (`scripts/ops/run_daily_batch.py`, or the nightly job) picks them up |
| `service_bus` | A consumer is woken within seconds |

`requested_item_count` is the number of items **this** run owns. It can be
lower than the OPEN order count: if an order already has an in-flight item
from an earlier run, that item stays with its original run rather than
being double-counted here.

#### `GET /batches/{job_run_id}`

`404` if the run does not exist.

```json
{
  "job_run_id": "0f3c…",
  "requested_item_count": 4,
  "counts": {"PENDING": 1, "RUNNING": 1, "SUCCEEDED": 2, "DEAD": 0},
  "total_items": 4,
  "is_complete": false
}
```

`is_complete` is `SUCCEEDED + DEAD == total_items`, reconciled against rows
that actually exist rather than `requested_item_count` (a pre-enqueue
forecast). A run with zero items is never complete -- it never started.

#### `GET /batches/{job_run_id}/items`

| Param | Type | Default |
|---|---|---|
| `status` | `PENDING` \| `RUNNING` \| `SUCCEEDED` \| `DEAD` | all |
| `limit` | 1–200 | 50 |
| `offset` | ≥ 0 | 0 |

```bash
curl "http://127.0.0.1:8000/api/v1/batches/0f3c…/items?status=DEAD"
```

Each item carries `order_id`, `task_type`, `status`, `attempt_count`,
`max_attempts`, `last_error_code`, and timestamps. **`last_error_message`
is derived from the error code, never the raw exception text** -- consistent
with the no-internal-detail rule in "Errors" above. For the real exception,
read `job_item.last_error` in the database or the worker logs.

### Admin

Router: `app/api/v1/admin.py`. Both idempotent -- safe to call repeatedly
to reset a demo environment.

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/seed-master-data` | Seed retailers/SKUs/locations/carriers/rules + the 4 example orders |
| POST | `/admin/simulate-daily-run` | Replay all 4 worked-example scenarios day-by-day through the real service layer |
