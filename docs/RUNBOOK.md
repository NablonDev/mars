# Runbook

Operational reference for running, deploying, and troubleshooting the CMIR Resolution
Agent and PO Validation Agent. See `README.md` for first-time setup and `CLAUDE.md` for
architecture. `API.md`/`DATABASE.md` cover the HTTP and data-model surfaces this runbook
assumes.

## Deployed components

Three independently-running processes, sharing one Postgres database:

1. **FastAPI app** (`app.main:app`) — the only process that runs LangGraph. Handles
   reviewer HTTP traffic and `/api/v1/internal/process-email` (the queue-consumer's
   forwarding target).
2. **Azure Function** (`function_app.py`, timer-triggered every minute) — claims new
   `email_events` rows and enqueues them to Azure Service Bus. Does **not** run any
   workflow logic itself; trigger definitions live in `azure_functions/` as Blueprints
   registered onto the root `FunctionApp()`.
3. **Service Bus consumer** (`app/workers/service_bus_consumer.py`, run via `python -m
   app.workers.service_bus_consumer`) — a standalone long-running listener. Deserializes
   each queue message and forwards it over HTTP to the FastAPI process; never touches
   the graph or Postgres directly.

All LangGraph execution — queue-driven or reviewer-driven — funnels through the one
FastAPI process, keeping checkpoint state centralized.

## Running locally

```bash
uv sync                                   # or: pip install -r requirements.txt
cp .env.example .env                      # fill in real credentials
docker compose up -d postgres             # or point DB_* at any reachable Postgres
alembic upgrade head                      # fresh DB only -- see CLAUDE.md if not fresh
uvicorn app.main:app --reload             # http://127.0.0.1:8000, docs at /docs
```

Run the Service Bus consumer separately when testing the async queue path:
```bash
python -m app.workers.service_bus_consumer
```

Tests:
```bash
python -m unittest discover -s tests/unit          # no live dependencies
docker compose up -d postgres
python -m unittest discover -s tests/integration   # real Postgres; skips if unreachable
```

## Required configuration

Loaded once into typed dataclasses in `app/core/config.py`. Minimum required:
`EMAIL_USERNAME`/`EMAIL_PASSWORD`/`IMAP_SERVER`, `DB_HOST`/`DB_NAME`/`DB_USER`/
`DB_PASSWORD`, `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`/
`AZURE_OPENAI_DEPLOYMENT_NAME`. Service Bus needs
`SERVICEBUS_FULLY_QUALIFIED_NAMESPACE`/`SERVICE_BUS_CONNECTION_STRING` for anything
beyond local defaults. **Never commit `.env` or `local.settings.json`** — rotate any
credential that leaks outside a secrets manager.

## Troubleshooting

### Thread update/decision returns `THREAD_STALE` (409)
Another reviewer (or the system) updated the thread after the client last fetched it.
Fetch `GET /threads/{thread_id}/stage` or `.../snapshot` for the current
`updated_at` and retry with that value as `expected_updated_at`.

### Thread action returns `THREAD_NOT_WAITING` (409)
The thread isn't paused at the stage that API expects. Check
`workflow_threads.status`/`stage` — missing-fields APIs require
`waiting_missing_fields`; update/decision APIs require `waiting_approval`; PO
Validation's resume APIs require `waiting_manual_cmir_entry` /
`waiting_qty_mismatch_decision` respectively.

### Decision returns `CMIR_VERSION_CONFLICT` (409)
Another thread's approval already superseded the active `cmir_records` row for this
customer/material while this thread was pending. The thread closes to
`COMPLETED_CONFLICT`/`completed_conflict` — it does **not** reopen for retry
automatically (open product question, see `docs/cmir_scd2_versioning.md`). Fetch
`GET /threads/{thread_id}/snapshot` for the thread that actually won, or start a new
one, against the current record.

### PO Validation resume returns `MATERIAL_NOT_FOUND` (422)
The reviewer-submitted/chosen SAP material number has no `material_master` row for
that plant. This is checked *before* the graph is touched — nothing was written to
`po_lines`/`po_line_errors` for this attempt. Confirm the material/plant combination
against the SAP mirror sync, or ask the reviewer to pick a different material.

### Gmail ingest returns no emails
Check, in order: `filters.subject_contains`, `filters.unread_only`, the Gmail app
password (not the normal account password), IMAP enabled on the account,
`EmailConfig.lookback_days`, and whether the target emails are actually unread when
`unread_only=true`.

### A thread is stuck at `FAILED` with `current_node: persist_email`
The graph raised before `email_id`/the `workflow_threads` row could be created
(`CMIRRunService._process_email_thread`'s exception path). Check `agent_runs.error`
for the underlying exception message — usually a Postgres connectivity issue or a
constraint violation on `email_events`/`cmir_records`.

### Service Bus consumer keeps abandoning messages
`app/workers/service_bus_consumer.py::_process_message` abandons (rather than
completes) any message where the HTTP forward to `/internal/process-email` raises —
check the FastAPI process's logs for the actual failure, not the consumer's. The
consumer retries its receive loop with a 5s backoff on connection-level errors.

## Useful debug queries

Reviewer queue backlog:
```sql
SELECT thread_id, stage, status, updated_at
FROM workflow_threads
WHERE status NOT LIKE 'completed%'
ORDER BY updated_at DESC;
```

Open pending actions older than expected (possible stuck reviews):
```sql
SELECT thread_id, interrupt_type, created_at
FROM pending_human_actions
WHERE status = 'open'
ORDER BY created_at ASC;
```

Per-node timing/failures for one run:
```sql
SELECT node_name, status, duration_ms, error
FROM agent_trace
WHERE run_id = :run_id
ORDER BY started_at ASC;
```

Current CMIR mapping for one customer/material (post-SCD2):
```sql
SELECT * FROM cmir_records
WHERE customer_identity_key = UPPER(REGEXP_REPLACE(:customer_identity, '[^A-Za-z0-9]', '', 'g'))
  AND target_customer_material_ref_key = UPPER(REGEXP_REPLACE(:material_ref, '[^A-Za-z0-9]', '', 'g'))
  AND is_current;
```

## Deployment notes

- `function_app.py`, `host.json`, `local.settings.json` must stay at the repo root —
  Azure Functions Core Tools and the Functions runtime discover them there by
  convention, not via configuration.
- `requirements.txt` (not `pyproject.toml`/`uv.lock`) is what Azure Functions' Python
  deployment model builds from — keep it regenerated (`uv export`) after any dependency
  change, or the Function App deployment silently uses stale versions.
- Already-deployed databases: run `alembic stamp head` before ever running `alembic
  upgrade` against them — their base tables predate this repo's Alembic conversion and
  re-running the bootstrap revision against live data is not what you want.
