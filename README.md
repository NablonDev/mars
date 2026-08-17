# Mars Petcare — Projected Fines System

Forecasts, ahead of delivery, the retailer chargebacks Mars Petcare is
likely to incur on open orders — driven by production shortfalls and
shipment delays. A deterministic rules engine computes the projection;
an LLM-powered endpoint can explain, in plain language, why a given
order's number is what it is.

## Stack

Python 3.12+, FastAPI, PostgreSQL (SQLAlchemy 2.0 + Alembic), Azure
OpenAI for the fine-summary feature.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # edit DATABASE_URL (Postgres or SQLite) and, if
                       # you want the LLM feature, AZURE_OPENAI_*

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

Seed demo data and try it out:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/seed-master-data
curl -X POST http://127.0.0.1:8000/api/v1/admin/simulate-daily-run
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/run \
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

The API can enqueue a batch too (`POST /api/v1/batches/run`), but under the
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

Runs against an in-memory SQLite database — no live Postgres required.

## Docs

Start here:

- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — **how to run the whole system**: local
  workflow with the queue, every config variable, and the Azure build sheet
- [`docs/JOB-QUEUE-WALKTHROUGH.md`](docs/JOB-QUEUE-WALKTHROUGH.md) — code tour of the
  queue, for someone seeing it for the first time
- [`docs/ASYNC-EXECUTION.md`](docs/ASYNC-EXECUTION.md) — *why* the queue is designed
  the way it is, and when to change it

Reference:

- [`docs/API.md`](docs/API.md) — every endpoint, request/response shapes, error codes
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — setup, seeding, troubleshooting, in depth
- [`docs/DATABASE.md`](docs/DATABASE.md) — schema, migrations, conventions
- [`docs/FINE_ENGINE.md`](docs/FINE_ENGINE.md) — the projection calculation itself
- [`docs/DOCKER.md`](docs/DOCKER.md) — image internals

## Layout

```
app/
  main.py               -- FastAPI app factory
  api/v1/                 -- routers
  services/                 -- business logic (projection, seeding, fine summary)
    fine_projection/          -- pure calculation, no SQLAlchemy/FastAPI
  agents/                     -- LLM layer: providers, prompts, tools
  queue/                        -- dispatch backends behind one Protocol
  workers/                        -- the claim/execute/settle loop
  models/                           -- SQLAlchemy ORM + enums
  repositories/                        -- database access
  schemas/                               -- Pydantic request/response models
alembic/                -- migrations
tests/unit/             -- pytest (in-memory SQLite)
tests/integration/      -- pytest against a live Postgres
scripts/ops/            -- operational entry points (nightly batch, CLI)
scripts/demo/           -- seed and demo scripts
```
