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
fine-projection-summary feature).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
uv sync   # or: pip install -e .

cp .env.example .env   # edit DATABASE_URL, and AZURE_OPENAI_* / EMAIL_* /
                        # SERVICE_BUS_* / JOB_QUEUE_* for the features you're running

alembic upgrade head
```

## Running

There is one image and one codebase, but **two things you can run**. Which
one you want depends on whether you are serving requests or processing the
day's backlog.

### The API

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
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/projections/runs \
  -H "Content-Type: application/json" -d '{}'
```

### The batch worker

Every OPEN order, projected and summarised concurrently through a durable
queue. In production this is an Azure Container Apps Job on a nightly cron;
locally it is the same script:

```bash
python scripts/ops/run_daily_batch.py            # enqueue + drain
python scripts/ops/run_daily_batch.py --dry-run  # count only, writes nothing
```

The API can enqueue a batch too (`POST /api/v1/batches/runs`), but under the
default `postgres` backend that only writes the ledger rows — nothing runs
them until a drain happens. Watch progress with
`GET /api/v1/batches/{job_run_id}`.

### A single order, no server

```bash
python scripts/ops/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05
python scripts/ops/run_projection_cli.py --all-open
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

Start here:

- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — **how to run the whole system**: local
  workflow with the queue, every config variable, and the Azure build sheet
- [`docs/JOB-QUEUE-WALKTHROUGH.md`](docs/JOB-QUEUE-WALKTHROUGH.md) — code tour of the
  queue, for someone seeing it for the first time

Reference:

- [`docs/API.md`](docs/API.md) — every endpoint, request/response shapes, error codes
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — setup, seeding, troubleshooting, in depth
- [`docs/DATABASE.md`](docs/DATABASE.md) — schema, migrations, conventions
- [`docs/DOCKER.md`](docs/DOCKER.md) — image internals

## Layout

```
app/
  main.py                 -- FastAPI app factory
  api/v1/                   -- routers (cmir + po_validation + fines + batches)
  core/                       -- config, exceptions, container (CMIR composition root), rate_limit
  services/                     -- business logic (cmir_run_service, po_validation_service, fine_projection_service, fine_seeding, fine_projection_summary, ...)
    fine_projection/                -- pure calculation, no SQLAlchemy/FastAPI
  agents/                            -- LLM/LangGraph layer: providers/ (shared), cmir/, po_validation/, and fine_projection_summary/ (domain-first, one folder per agent)
  queue/                               -- fines job-queue dispatch backends behind one Protocol, plus the CMIR Service Bus producer
  workers/                              -- the fines claim/execute/settle loop, plus the CMIR Service Bus consumer
  models/                                 -- SQLAlchemy ORM + enums (cmir schema + fines schema)
  repositories/                            -- database access
  schemas/                                   -- Pydantic request/response models
alembic/                -- migrations (single linear history across every schema)
tests/unit/             -- pytest (in-memory SQLite)
tests/integration/      -- pytest against a live Postgres
scripts/ops/            -- operational entry points (nightly batch, CLI)
scripts/demo/           -- seed and demo scripts
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
