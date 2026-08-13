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

## Errors

Every deliberate error uses one envelope:

```json
{"error": {"code": "ORDER_NOT_FOUND", "message": "No order found with order_id='NOPE-999'"}}
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

## Master data

Thin create+list pairs over `MasterDataRepository`, no update/delete
(dimension data, rarely churns). Router: `app/api/v1/master_data.py`.

| Method | Path |
|---|---|
| POST / GET | `/retailers` |
| POST / GET | `/skus` |
| POST / GET | `/locations` |
| POST / GET | `/carriers` |

## Fine rules

Router: `app/api/v1/fine_rules.py`. `POST /fine-rules` accepts an
optional `tiers` array in the request body for `calc_type: "TIERED"`
rules (see `docs/FINE_ENGINE.md` "Pricing the fine").

| Method | Path |
|---|---|
| POST | `/fine-rules` |
| GET | `/fine-rules?retailer_id=` |

## Orders and facts

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

## Projections

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

## Fine Summaries

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

## Run projection + summary together

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

## Admin

Router: `app/api/v1/admin.py`. Both idempotent -- safe to call repeatedly
to reset a demo environment.

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/seed-master-data` | Seed retailers/SKUs/locations/carriers/rules + the 4 example orders |
| POST | `/admin/simulate-daily-run` | Replay all 4 worked-example scenarios day-by-day through the real service layer |
