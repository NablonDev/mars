# Mars Petcare — CMIR Resolution System & Projected Fines

Two agentic backends in one FastAPI app, separated by Postgres schema:

- **CMIR Resolution Agent** (`cmir` schema) — processes inbound CMIR emails,
  extracts CMIR draft data with Azure OpenAI via a LangGraph workflow, pauses
  for human review, and persists approved records in Postgres. Also runs a
  PO Validation agent against the same email-ingest/HITL infrastructure.

- **Projected Fines** (`fines` schema) — forecasts, ahead of delivery, the
  retailer chargebacks Mars Petcare is likely to incur on open orders,
  driven by production shortfalls and shipment delays. A deterministic
  rules engine computes the projection; an LLM-powered endpoint can explain,
  in plain language, why a given order's number is what it is.

## Stack

Python 3.12+, FastAPI, PostgreSQL (SQLAlchemy 2.0 + Alembic), LangGraph
(CMIR/PO-validation workflows, PostgreSQL checkpointer), Azure Service Bus
(CMIR mail-processing queue), Azure OpenAI (CMIR extraction and the
fine-summary feature).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
uv sync   # or: pip install -e .

cp .env.example .env   # edit DATABASE_URL, and AZURE_OPENAI_* / EMAIL_* /
                        # SERVICE_BUS_* for the features you're running

alembic upgrade head
```

## Running

```bash
uvicorn app.main:app --reload
```

Interactive docs: `http://127.0.0.1:8000/docs`. Health check:
`curl http://127.0.0.1:8000/api/v1/health`.

Start a CMIR email-ingest batch:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/emails \
  -H "Content-Type: application/json" \
  -d '{"max_workers":4,"source":"gmail","filters":{"subject_contains":"CMIR","unread_only":true}}'
```

List the CMIR reviewer queue: `GET /api/v1/runs?view=threads`.

Seed fines demo data and try it out:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/seed-master-data
curl -X POST http://127.0.0.1:8000/api/v1/admin/simulate-daily-run
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/run \
  -H "Content-Type: application/json" -d '{}'
```

Or run a projection without a server, straight against the database:

```bash
python scripts/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05
python scripts/run_projection_cli.py --all-open
```

## Tests

```bash
pytest -v
```

Runs against an in-memory SQLite database (both `cmir` and `fines` schemas
translated away for SQLite, see `app/db/session.py`) — no live Postgres
required. `tests/integration/` exercises a real Postgres connection and
skips (rather than failing) when one isn't reachable at `DATABASE_URL`.

## Docs

- [`docs/API.md`](docs/API.md) — every endpoint, request/response shapes, error codes
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — setup, running, seeding, troubleshooting, in depth
- [`docs/DATABASE.md`](docs/DATABASE.md) — schema, migrations, conventions

## Layout

```
app/
  main.py                 -- FastAPI app factory
  api/v1/                   -- routers (cmir + po_validation + fines)
  core/                       -- config, exceptions, container (CMIR composition root)
  services/                     -- business logic (cmir_run_service, po_validation_service, projection, seeding, fine summary, ...)
  agents/                          -- LLM/LangGraph layer: providers, prompts, tools, cmir/ and po_validation/ graphs
  models/                            -- SQLAlchemy ORM (cmir schema + fines schema)
  repositories/                        -- database access
  schemas/                               -- Pydantic request/response models
  queue/, workers/                         -- Azure Service Bus producer/consumer
alembic/                -- migrations (single linear history across both schemas)
tests/                  -- pytest + unittest (in-memory SQLite; tests/integration/
                           needs a real Postgres)
scripts/                -- seed/demo/CLI scripts
```

## CMIR Resolution Agent — feature notes

The current implementation follows `docs/prd.md`: one backend ingest batch
can process many emails, and each email-derived review workflow gets its
own UI-facing `thread_id`.

**Important identity rule**: reviewer/UI actions must use `thread_id` — not
sender email, `batch_id`, `agent_run_id`, or `email_id`. One batch can
contain multiple emails, and multiple emails can come from the same sender.

Main endpoints (base path `/api/v1`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ingest/emails` | Start one email ingest batch. |
| `GET` | `/runs?view=threads` | Reviewer queue: list workflow threads. |
| `GET` | `/runs?view=agents` | Ops view: list per-email agent runs. |
| `GET` | `/runs?view=batches` | Ops view: list batch rollups by `batch_id`. |
| `GET` | `/threads/{thread_id}/stage` | Get current stage/status for one thread. |
| `GET` | `/threads/{thread_id}/snapshot` | Get email, CMIR draft, and HITL history. |
| `POST` | `/threads/{thread_id}/missing-fields` | Submit missing mandatory fields and resume graph. |
| `POST` | `/threads/{thread_id}/update` | Save reviewer draft edits. |
| `POST` | `/threads/{thread_id}/decision` | Approve or reject a draft. |

Common error responses (`ServiceError`, see `app/core/exceptions.py`):

- `THREAD_STALE` — refetch the latest `updated_at` and retry as `expected_updated_at`.
- `THREAD_NOT_WAITING` — missing-fields requires `waiting_missing_fields`;
  update/decision require `waiting_approval` (check `workflow_threads.status`).
- `CMIR_VERSION_CONFLICT` (HTTP 409) — another thread's approval already
  superseded the active `cmir_records` row for this customer/material while
  this thread was waiting. The thread closes to `COMPLETED_CONFLICT` and does
  not reopen automatically; fetch the winning thread's snapshot or start a new one.

Gmail note: `EMAIL_USERNAME`/`EMAIL_PASSWORD` need a Gmail app password, not
the normal account password, with IMAP enabled.

Debugging guide: open `docs/debug_flow.html` in a browser for the full
ingest-flow walkthrough, LangGraph node sequence, and common-failure table.

## Security

Never commit `.env` or real credentials. Rotate any Gmail app password,
database password, Azure OpenAI key, or Service Bus connection string that
has been shared outside a secure secret manager.
