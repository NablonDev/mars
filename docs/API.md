# API

All endpoints are mounted under `/api/v1`. Interactive docs (Swagger UI) are
served at `/docs` when the app is running (`uvicorn app.main:app`) and
`APP_DOCS_ENABLED=true` -- closed by default, see `docs/DEPLOYMENT.md`
"Configuration reference". Full request/response schemas: `app/schemas/`.
Router implementations: `app/api/v1/`.

## Response envelope

Every route returns the same `{success, message, data, error}` shape
(`app/core/envelope.py`), with one exception noted below:

```json
{ "success": true, "message": "OK", "data": { "...": "..." }, "error": null }
```

```json
{ "success": false, "message": "Unknown thread_id.", "data": null,
  "error": { "code": "THREAD_NOT_FOUND", "details": { "thread_id": "..." } } }
```

`GET /api/v1/health` is the one route in the whole API that returns a bare
body instead of the envelope (see below) -- it has to keep working for load
balancers that only check the HTTP status and don't parse JSON.

## Authentication

Every route requires the `X-Internal-Api-Key` header, checked against
`APP_INTERNAL_API_KEY` (`app/api/dependencies.py::require_internal_api_key`,
via `secrets.compare_digest`, wired in globally at the router-aggregation
point in `app/api/router.py` -- new routers are covered automatically,
nothing per-route to remember). The one exception is `GET /api/v1/health`,
left open for load balancers/uptime monitors. Missing or wrong key ->
`401`, reshaped into the standard envelope as `error.code = "HTTP_401"`; it
never echoes what was sent or what was expected.

```bash
curl http://127.0.0.1:8000/api/v1/purchase-orders -H "X-Internal-Api-Key: $INTERNAL_API_KEY"
```

Every curl example below omits this header for brevity -- add it to every
call except `/health`.

## Error codes

Every expected error raises one of six `AppError` subclasses
(`app/core/exceptions.py`), each constructed with a `code: str` rather than
a dedicated class per failure case:

| Class | HTTP status | Example `code` values |
|---|---|---|
| `NotFoundError` | 404 | `PO_NOT_FOUND`, `PROJECTION_NOT_FOUND`, `MITIGATION_OPTION_NOT_FOUND`, `JOB_RUN_NOT_FOUND`, `THREAD_NOT_FOUND`, `EMAIL_NOT_FOUND`, `CARRIER_NOT_FOUND`, `PO_DELIVERY_CHANGE_REQUEST_NOT_FOUND` |
| `ConflictError` | 409 | `THREAD_STALE`, `CMIR_VERSION_CONFLICT`, `ACTIVE_PO_DELIVERY_CHANGE_REQUEST_EXISTS`, PO-uniqueness conflicts |
| `ValidationError` | 422 | `VALIDATION_ERROR`, `VIEW_NOT_SUPPORTED`, `MATERIAL_NOT_FOUND`, `PO_HAS_NO_LINES`, `INVALID_AS_OF_DATE`, `INVALID_PO_DELIVERY_CHANGE_RESPONSE`, `INVALID_INCLUDE` |
| `BusinessRuleError` | 409 | `NO_PROJECTION_EXISTS`, `NO_ACTIVE_RULES`, `NO_MITIGATION_OPTIONS_EXIST`, `PO_DELIVERY_CHANGE_LEAD_TIME_ERROR` |
| `ExternalServiceError` | 502 | `WORKFLOW_RESUME_FAILED`, `WORKFLOW_STATE_CORRUPT`, `QUEUE_NOT_CONFIGURED` |
| `NotAuthenticatedError` | 401 | Declared for the contract; `require_internal_api_key` currently raises a bare FastAPI `HTTPException(401)` instead, reshaped to `code="HTTP_401"` by the generic handler |

Also normalized into the same envelope: FastAPI's native `RequestValidationError`
(`code="REQUEST_VALIDATION_ERROR"`, 422), any bare `HTTPException`
(`code=f"HTTP_{status_code}"`), and an unhandled exception
(`code="INTERNAL_ERROR"`, 500, generic message only -- never a stack trace).
`details` is only present on a 4xx; a 5xx withholds it entirely, since some
call sites put internal failure text there.

## Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Readiness check, including DB connectivity |

`200` with `{"status": "ok", "database": "ok"}` when a `SELECT 1` over a
short-lived pooled connection succeeds; `503` with
`{"status": "degraded", "database": "unreachable"}` when it doesn't. No
driver or connection detail is ever returned -- it's logged instead. Not
wrapped in the `{success, message, data, error}` envelope (see above).

## Common (master data, purchase orders, fulfillment facts)

Routes in `app/api/v1/common/`, backed by `app/repositories/common/`.

### Master data

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/retailers` | Create retailer |
| GET | `/api/v1/retailers` | List retailers |
| POST | `/api/v1/retailers/{retailer_id}/locations` | Create a retailer-owned location |
| GET | `/api/v1/retailers/{retailer_id}/locations` | List a retailer's locations |
| POST | `/api/v1/skus` | Create SKU |
| GET | `/api/v1/skus` | List SKUs |
| POST | `/api/v1/materials` | Create material (plant-agnostic identity) |
| GET | `/api/v1/materials` | List materials |
| POST | `/api/v1/material-masters` | Create material master (one row per `(material, plant)`) |
| GET | `/api/v1/material-masters` | List material masters |
| POST | `/api/v1/plants` | Create plant |
| GET | `/api/v1/plants` | List plants |
| POST | `/api/v1/carriers` | Create carrier |
| GET | `/api/v1/carriers` | List carriers |
| GET | `/api/v1/carriers/{carrier_id}` | Get one carrier (`404 CARRIER_NOT_FOUND`) |

All creates return `201` with the created row (request fields plus `id`);
list routes take no query filters except where noted.

### Purchase orders and fulfillment facts

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/purchase-orders` | Create PO header + lines |
| GET | `/api/v1/purchase-orders?order_status=` | List purchase orders, optionally filtered by status |
| POST | `/api/v1/purchase-orders/{purchase_order_id}/confirmations` | Record an order confirmation (header + lines) |
| GET | `/api/v1/purchase-orders/{purchase_order_id}/confirmations` | Flat per-line confirmation history for the PO |
| POST | `/api/v1/purchase-orders/{purchase_order_id}/shipments` | Record a shipment (auto-creates the `delivery` header) |
| GET | `/api/v1/purchase-orders/{purchase_order_id}/shipments` | List shipments for the PO |
| POST | `/api/v1/purchase-orders/{purchase_order_id}/demand-exceptions` | Flag a demand exception (`422 PO_HAS_NO_LINES` if the PO has no lines and none is given) |
| GET | `/api/v1/purchase-orders/{purchase_order_id}/demand-exceptions` | List demand exceptions across the PO's lines |
| POST | `/api/v1/purchase-orders/{purchase_order_id}/actual-penalties` | Record an actual (post-delivery) penalty |
| GET | `/api/v1/purchase-orders/{purchase_order_id}/actual-penalties` | List actual penalties for the PO |

Every create route returns `201`; every list route returns `200` with a
plain JSON array as `data`.

## Penalties

Routes in `app/api/v1/penalties/`, backed by `app/services/penalties/`.

### Rules

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/penalty-rules` | Create a penalty rule, with tiers when `calc_type="TIERED"` |
| GET | `/api/v1/penalty-rules?retailer_id=` | List penalty rules, optionally by retailer |

### Projections

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/purchase-orders/{purchase_order_id}/penalty-projections` | Compute and persist a projection for one PO (synchronous) |
| GET | `/api/v1/purchase-orders/{purchase_order_id}/penalty-projections?include=summary` | Projection history for one PO, optionally with each row's cached summary |
| GET | `/api/v1/penalty-projections?status=OPEN` | Flat cross-PO projection list |
| GET | `/api/v1/penalty-projections/{projection_id}?include=summary` | Single projection read (`404 PROJECTION_NOT_FOUND`) |
| GET | `/api/v1/purchase-orders/{purchase_order_id}/penalty-exposure` | Latest projection's total-expected-penalty summary (`404 NO_PROJECTION_EXISTS` if none exist yet) |
| POST | `/api/v1/purchase-orders/{purchase_order_id}/penalty-projections/summary` | Trigger/poll the LLM projection-summary job (`200` with a cached/ready summary, or `202 PENDING`) |

`?include=` is validated against a per-route allow-list (only `summary` is
ever accepted here) and is a pure read -- it never schedules generation as a
side effect; `202`-returning routes signal it explicitly via `response.status_code`,
not a raised exception.

**Response shape -- probability, raw amount, and combined figure are always
three separate numbers, never just one blended figure.** Every violation
(in the live `POST .../penalty-projections` response, and in every persisted
row returned by the `GET` routes above, including `.../penalty-exposure`)
carries:

- `probability` / `failure_probability` -- the raw probability of the
  violation occurring, 0-1.
- `penalty_amount` -- the raw dollar amount the retailer would charge **if**
  the violation occurs (same field name in both the live-run response and
  every persisted row). Not probability-weighted.
- `expected_penalty_amount` -- `probability * penalty_amount`, a
  risk-adjusted decision-support figure. **Never a predicted or guaranteed
  cost** -- it is what the exposure is worth in expectation, not what will
  be billed.

`.../penalty-exposure`'s `total_expected_penalty_amount` is the `SUM`/`MAX`
(per the retailer's `stacking_mode`) of the latest projection date's
per-violation `expected_penalty_amount` figures. Like the per-violation
figure it aggregates, it
is a risk-adjusted estimate, not a certain amount -- decompose it back into
per-violation probability + raw amount via the `violations` array whenever
the underlying components matter, rather than treating the total as a single
authoritative number.

### Mitigations

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/penalty-mitigations?projection_id=` | List ranked mitigation options for a projection |
| POST | `/api/v1/penalty-mitigations?projection_id=` | Compute and persist ranked mitigation options (no request body -- `projection_id` is the only input) |
| GET | `/api/v1/penalty-mitigations/{mitigation_id}?include=summary` | Single mitigation option read (`404 MITIGATION_OPTION_NOT_FOUND`) |
| POST | `/api/v1/purchase-orders/{purchase_order_id}/penalty-mitigations/summary` | Trigger/poll the LLM mitigation-summary job (`200`/`202`, same contract as the projection summary above) |

### Delivery-change requests

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/purchase-orders/{purchase_order_id}/delivery-change-requests` | Open a delivery-date negotiation (`reason_code` is `SHORTAGE`\|`DELAY`\|`OTHER`) |
| GET | `/api/v1/purchase-orders/{purchase_order_id}/delivery-change-requests` | List a PO's delivery-change-request history |
| POST | `/api/v1/purchase-orders/{purchase_order_id}/delivery-change-requests/{request_id}/response` | Record the retailer's response (`ACCEPTED`\|`COUNTERED`\|`REJECTED`); re-runs the projection engine, so an accepted/countered response can legitimately surface `409 NO_ACTIVE_RULES` if no rule covers the retailer |

## Job runs (shared: `penalties` + `cmir`)

Routes in `app/api/v1/penalties/batches.py`. `job_run`/`job_item` live in the
`process` schema and are shared infrastructure -- one generic endpoint set,
not one per domain.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/job-runs` | Dispatch a batch job; `job_type` selects the domain (`PENALTY_PROJECTION_BATCH` default, or `CMIR_EMAIL_INGEST`) |
| GET | `/api/v1/job-runs/{job_run_id}` | Run status: counts by item status, `total_items`, `is_complete` (`404 JOB_RUN_NOT_FOUND`) |
| GET | `/api/v1/job-runs/{job_run_id}/items?status=&limit=&offset=` | List the items in one run |

`POST /api/v1/job-runs` always returns `202`. Under `job_type=PENALTY_PROJECTION_BATCH`
this only writes the ledger row -- nothing runs it until a drain happens
(`JOB_QUEUE_BACKEND=postgres`) or a consumer picks up the dispatched message
(`JOB_QUEUE_BACKEND=service_bus`); see `docs/JOB-QUEUE-WALKTHROUGH.md`.
`job_type=CMIR_EMAIL_INGEST` runs synchronously but still returns `202` for
response-contract uniformity. There is currently no "list all job runs"
endpoint -- only single-run lookups.

## CMIR

Routes in `app/api/v1/cmir.py`, backed by `app.services.cmir.run_service.CMIRRunService`.
See `docs/prd.md` for the business rules and the README's identity rule
before touching reviewer/queue code -- `thread_id` is the only identifier
reviewer/UI actions may key on.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/cmir/email-events` | Start one email-ingest batch (`202`; `source` must be `"gmail"` or `422 VALIDATION_ERROR`) |
| POST | `/api/v1/internal/process-email` | Internal Service Bus consumer callback, not PRD-facing -- polymorphic result shape, deliberately untyped |

## PO Validation

Routes in `app/api/v1/po_validation.py`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/po-validation/purchase-order-lines` | Ingest PO lines into the validation pipeline (runs CMIR-matching/material-master checks as a side effect); `202` |
| GET | `/api/v1/purchase-order-lines?status=&limit=&cursor=` | Flat cross-PO line listing (`422 VIEW_NOT_SUPPORTED` -- not implemented on the repository yet) |
| GET | `/api/v1/purchase-orders/{purchase_order_id}/lines` | Nested single-PO line listing |

## Workflow threads (shared: `cmir` + `po_validation`)

Routes in `app/api/v1/workflow_threads.py`. `workflow_thread` is a shared
`process`-schema resource used by both domains, not owned by either router.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/workflow-threads?domain=&status=&stage=&limit=&cursor=` | List workflow threads (`domain` is `cmir`\|`po_validation`) |
| GET | `/api/v1/workflow-threads/{thread_id}?include=snapshot` | Thread stage (always) plus its domain-specific snapshot (opt-in via `include`) |
| POST | `/api/v1/workflow-threads/{thread_id}/missing-fields` | Submit missing mandatory fields and resume the graph (CMIR-only in substance today) |
| PATCH | `/api/v1/workflow-threads/{thread_id}/draft` | Save reviewer draft edits (CMIR-only in substance today) |
| POST | `/api/v1/workflow-threads/{thread_id}/decisions` | Record a decision -- one generic endpoint, `decision_type` discriminator selects `CMIR_APPROVAL`, `QTY_MISMATCH`, or `MANUAL_CMIR_ENTRY` |

Common errors: `THREAD_NOT_FOUND` (404, unknown `thread_id`), `THREAD_STALE`
(409, `expected_updated_at` didn't match -- refetch and retry), `THREAD_NOT_WAITING`
(409, the thread isn't paused for that specific action), `CMIR_VERSION_CONFLICT`
(409, another approval already superseded the active `cmir_records` row for
this customer/material while this thread was waiting -- the thread closes to
`COMPLETED_CONFLICT` and does not reopen automatically).

## Processing errors (shared: `cmir` + `po_validation`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/processing-errors?purchase_order_line_id=` | List processing errors for one PO line (`purchase_order_line_id` is required) |

`processing_error` is a `process`-schema table shared across domains
(generalizes the old `po_line_errors`).

## Admin

Routes in `app/api/v1/admin.py`, backed by `app.services.seeding.service.PenaltySeedingService`.
Spans both domains, so it stays a top-level module rather than living under
`common/` or `penalties/`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/admin/seed-master-data?force=` | Seed the four worked-example scenarios' master/master-adjacent data |
| POST | `/api/v1/admin/simulate-daily-run` | Replay the seeded scenarios day by day, returning each day's projected shortage/delay penalty and negotiation outcome |
