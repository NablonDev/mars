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

Or run a projection without a server, straight against the database:

```bash
python scripts/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05
python scripts/run_projection_cli.py --all-open
```

## Tests

```bash
pytest -v
```

Runs against an in-memory SQLite database — no live Postgres required.

## Docs

- [`docs/API.md`](docs/API.md) — every endpoint, request/response shapes, error codes
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — setup, running, seeding, troubleshooting, in depth
- [`docs/DATABASE.md`](docs/DATABASE.md) — schema, migrations, conventions

## Layout

```
app/
  main.py               -- FastAPI app factory
  api/v1/                 -- routers
  services/                 -- business logic (projection, seeding, fine summary)
  agents/                     -- LLM layer: providers, prompts, tools
  models/                       -- SQLAlchemy ORM
  repositories/                    -- database access
  schemas/                           -- Pydantic request/response models
alembic/                -- migrations
tests/                  -- pytest (in-memory SQLite)
scripts/                -- seed/demo/CLI scripts
```
