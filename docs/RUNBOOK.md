# Runbook

Every way to set up, run, seed, exercise, and troubleshoot this service.
For *what* the system does and *why*, see `docs/FINE_ENGINE.md`; for
exactly how one database row turns into one dollar figure, with every
intermediate number shown, see `docs/HOW_IT_WORKS.md`; for *how the code
is laid out*, see `docs/legacy/root-CLAUDE.md` (the detailed architecture
map; `./CLAUDE.local.md` is the current working instructions file); for
endpoint-by-endpoint reference, see `docs/API.md`; for a presentation-ready
walkthrough, open `docs/architecture-walkthrough.html` directly in a
browser. This file is about running it.

## 1. Prerequisites

- Python 3.12+
- Either a Postgres instance, or nothing at all -- SQLite works for
  everything below except item 8 (a real Postgres deployment check).

## 2. First-time setup

```bash
git clone <this repo> && cd mars
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: point DATABASE_URL at Postgres, or leave the SQLite line
# uncommented for a zero-install local setup.
```

`app/core/config.py::Settings` reads `DATABASE_URL` from `.env` via
`pydantic-settings`. If `.env` doesn't exist or doesn't set it, the
default is the Postgres URL shown in `.env.example` -- so on a machine
with no Postgres running, always set `.env` (or export `DATABASE_URL`
directly) before doing anything else.

## 3. Database: migrate

```bash
alembic upgrade head          # run from the repo root, not app/
```

This creates every table in `docs/DATABASE.md`'s table list. Safe to
re-run (Alembic tracks the applied revision in `alembic_version`).

**Switching database backends** (e.g. SQLite for a quick local check
instead of Postgres): just change `DATABASE_URL` and re-run
`alembic upgrade head` against the new one -- migrations are backend-
agnostic (`docs/DATABASE.md` "Primary keys" -- the surrogate `id` type
works on both).

```bash
# One-off SQLite database in /tmp, no Postgres needed at all:
export DATABASE_URL="sqlite:////tmp/fines_demo.db"
alembic upgrade head
```

**Adding a new migration** after changing `app/models/`:

```bash
alembic revision -m "add whatever" --autogenerate   # needs a live DB connection
```

Then hand-check the generated file against `app/models/` before
committing -- `tests/test_migration_parity.py` will fail the build if
they disagree.

### The `fines` schema

On Postgres, every table in this project lives in a dedicated `fines`
schema, not `public` -- `public` is reserved for the shared cross-domain
tables the sibling cmir project owns. The schema name is declared once,
in `app/db/base.py::FINES_SCHEMA`, and `Base.metadata` is bound to it, so
nothing in `app/` needs to qualify a table name by hand.

Three consequences worth knowing:

- Alembic's own `alembic_version` bookkeeping table also lives in `fines`
  (`version_table_schema` in `alembic/env.py`), so this project's
  migration history can never collide with cmir's.
- `alembic/env.py` runs `CREATE SCHEMA IF NOT EXISTS fines` before
  Alembic touches its version table, so `alembic upgrade head` bootstraps
  a brand-new empty database with no manual setup.
- SQLite has no schemas at all. `app/db/session.py::apply_sqlite_schema_translation`
  translates `fines` away at the connection level (SQLAlchemy's
  `schema_translate_map`), which is why the SQLite paths -- the whole test
  suite, and the `sqlite:////tmp/fines_demo.db` recipe above -- keep
  working unchanged.

## 4. Running the API

```bash
uvicorn app.main:app --reload
```

- Interactive docs (try every endpoint from the browser): `http://127.0.0.1:8000/docs`
- Raw OpenAPI schema: `http://127.0.0.1:8000/openapi.json`
- Health check: `curl http://127.0.0.1:8000/api/v1/health`

The app builds its own `Database` from `Settings` at startup
(`app/main.py::create_app`) -- it does **not** run migrations for you.
Step 3 has to happen first, once, against whatever `DATABASE_URL`
you're pointing at.

## 5. Seeding the four worked examples

Two admin endpoints do this over HTTP -- no direct database writes. Both
are idempotent: safe to call repeatedly (e.g. to reset a demo).

```bash
# With the server running (step 4), in another terminal:
python scripts/seed_master_data.py                    # defaults to http://127.0.0.1:8000/api/v1
python scripts/seed_master_data.py --base-url http://localhost:9000/api/v1   # different host/port

python scripts/demo_daily_simulation.py               # replays all 4 scenarios, prints the day-by-day trend
```

Or hit the endpoints directly:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/seed-master-data
curl -X POST http://127.0.0.1:8000/api/v1/admin/simulate-daily-run
```

`seed-master-data` creates 2 retailers, 3 SKUs, 2 locations, 2 carriers,
4 fine rules, and 4 order headers (all `OPEN`). `simulate-daily-run`
walks all four orders through their entire scripted history
(`app/services/fine_projection/scenario_data.py`), writing each day's facts and running a
projection, then marks every order `DELIVERED`. Running it twice in a
row will re-simulate from scratch and re-mark everything `DELIVERED` --
that's expected, not an error.

## 6. Running projections

**Via the API** (what a real integration would call):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/projections/run \
  -H "Content-Type: application/json" \
  -d '{"order_id": "WMT-100234", "projection_date": "2026-08-05"}'

# Every open order, today:
curl -X POST http://127.0.0.1:8000/api/v1/projections/run \
  -H "Content-Type: application/json" -d '{"all_open": true}'

# Override the retailer's stacking policy for this run only:
curl -X POST http://127.0.0.1:8000/api/v1/projections/run \
  -H "Content-Type: application/json" \
  -d '{"order_id": "WMT-100234", "stacking_mode_override": "MAX"}'

curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/projections   # full dated history
curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/exposure      # latest total only

# Projection, then (only if it succeeds) the fine summary for the same
# day, in one call -- see docs/API.md "Run projection + summary together":
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/run \
  -H "Content-Type: application/json" -d '{}'
```

**Without a server** (batch/cron-style, straight against the database):

```bash
python scripts/run_projection_cli.py --order-id WMT-100234
python scripts/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05
python scripts/run_projection_cli.py --all-open
python scripts/run_projection_cli.py --all-open --date 2026-08-05 --stacking-mode MAX

# Same projection-then-summary sequencing as POST /orders/{id}/run above,
# for the no-HTTP-server path -- runs the fine summary inline (no
# BackgroundTasks needed in a one-shot process) right after each order's
# projection succeeds. Needs AZURE_OPENAI_* configured (step 8):
python scripts/run_projection_cli.py --all-open --with-summary
```

This is what a daily cron/Airflow task should call in production --
standing up an HTTP server just to run a scheduled batch job is
unnecessary overhead. No scheduler is actually provisioned yet
(documentation-only).

## 7. Feeding new facts (not one of the four scripted scenarios)

Create master data and an order, then post facts as they arrive:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/retailers \
  -H "Content-Type: application/json" \
  -d '{"retailer_id": "RET-TGT", "retailer_name": "Target", "stacking_mode": "SUM"}'

curl -X POST http://127.0.0.1:8000/api/v1/orders \
  -H "Content-Type: application/json" \
  -d '{"order_id": "TGT-1", "retailer_id": "RET-TGT", "sku_id": "SKU-PED30",
       "ship_from_location_id": "LOC-ATL", "order_qty": 500, "unit_price": 20.0,
       "order_date": "2026-09-01", "requested_delivery_date": "2026-09-10",
       "required_ship_date": "2026-09-08"}'

# A rule has to exist for the retailer or /projections/run returns 422:
curl -X POST http://127.0.0.1:8000/api/v1/fine-rules \
  -H "Content-Type: application/json" \
  -d '{"rule_id": "RULE-TGT-OTIF", "retailer_id": "RET-TGT", "violation_type": "OTIF_LATE",
       "calc_type": "PERCENT_OF_PO", "rate": 0.03}'

# SAP just cut the order:
curl -X POST http://127.0.0.1:8000/api/v1/orders/TGT-1/confirmations \
  -H "Content-Type: application/json" \
  -d '{"confirmation_id": "CONF-TGT-1-01", "confirmed_qty": 450,
       "confirmation_date": "2026-09-03T06:00:00"}'

curl -X POST http://127.0.0.1:8000/api/v1/projections/run \
  -H "Content-Type: application/json" -d '{"order_id": "TGT-1"}'
```

`PUT /orders/{id}/shipment` and `POST /production-schedule` work the
same way -- see `docs/API.md`. Every write is historized where the
schema calls for it (`docs/DATABASE.md` "Historization"), so posting the
same kind of fact again doesn't overwrite the last one, it adds to the
timeline `build_snapshot` reads "as of" a given date from.

## 8. Azure OpenAI configuration (for the fine-summary feature)

`POST /orders/{order_id}/summary` (see `docs/API.md` "Fine Summaries")
is the only thing in this codebase that calls out to an LLM. Everything
else works with no Azure credentials set at all. Generation runs as a
background job -- the `POST` itself never calls Azure OpenAI inline, so
missing/bad credentials surface as a `FAILED` status on
`GET /orders/{order_id}/summary`, not as a failure of the `POST`
itself.

```bash
# In .env (see .env.example) -- names must match app/core/config.py::Settings
# exactly, including the _NAME suffix on the deployment var. ENDPOINT is
# the full v1 API base URL (note the /openai/v1 suffix) -- no
# AZURE_OPENAI_API_VERSION var; the v1 GA API dropped the dated
# api-version param entirely (2026-08).
AZURE_OPENAI_API_KEY=<your key>
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/openai/v1
AZURE_OPENAI_DEPLOYMENT_NAME=<your deployment name>

# Optional -- defaults shown. AzureOpenAIChatClient passes both values
# straight through to the SDK's ChatOpenAI client -- no app-level retry
# wrapper on top, so the SDK's own retry logic is the only retry
# authority (an earlier version of this client stacked its own retry
# loop on top of the SDK's, which could multiply a slow call's wall-clock
# time well past AZURE_OPENAI_MAX_ATTEMPTS x AZURE_OPENAI_TIMEOUT_SECONDS
# -- that wrapper is gone). 3 attempts is safe with only one retry layer.
# Raise the timeout if a real deployment's latency runs long; only raise
# AZURE_OPENAI_MAX_ATTEMPTS further if you have evidence a retry actually
# recovers failures on your specific deployment (a genuinely transient
# blip, not normal variance):
AZURE_OPENAI_TIMEOUT_SECONDS=90
AZURE_OPENAI_MAX_ATTEMPTS=3
```

### Why a slow/flaky call surfaces as a background-job `FAILED` status, not a hang or a raw `500`

Generation runs inside `FastAPI.BackgroundTasks`, not inline with the
`POST`, so a failure -- upstream timeout, rate limit, bad credentials,
anything `ChatOpenAI.invoke()` can raise -- is caught by
`FineSummaryService.run_generation` and persisted as a `FAILED` row
rather than propagating into an HTTP response at all. The real
exception is logged server-side
(`app.services.fine_summary`, `logger.exception(...)`); only the
generic, client-safe `"Fine summary generation failed upstream"`
message reaches the row a client can poll, per this app's
message/detail split (`app/core/exceptions.py`).

With those set:

```bash
# Fast, no LLM call inline -- 200 (cache hit) or 202 (job scheduled):
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/summary \
  -H "Content-Type: application/json" -d '{}'

# Poll until status leaves PENDING:
curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/summary

# Force a fresh LLM call even if today's summary is already cached:
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/summary \
  -H "Content-Type: application/json" -d '{"force_regenerate": true}'
```

Or the scripted equivalent, which looks up each order's actual latest
projection date first rather than relying on today happening to fall
inside the mock scenarios' Aug 2026 date range:

```bash
python scripts/demo_fine_summary.py                          # every order on file
python scripts/demo_fine_summary.py --order-id WMT-100234     # one order
python scripts/demo_fine_summary.py --order-id WMT-100234 --force-regenerate
```

**The full tour, in one command** -- seed, replay all four scenarios,
then explain each order's final number, chained together
(`scripts/run_end_to_end_demo.py`):

```bash
python scripts/run_end_to_end_demo.py                   # needs Azure OpenAI creds for the last stage
python scripts/run_end_to_end_demo.py --skip-fine-summary  # engine-only, no LLM cost, no creds needed
```

Leave the `AZURE_OPENAI_*` vars unset/blank to run every other part of
the app normally -- `Settings` defaults them all to `""`, and the
fine-summary endpoint only fails (`AzureOpenAIConfigError`, surfaced as a
clean error, not a stack trace) the first time it's actually called, not
at app startup.

### Known limitation: no request-level rate limiting

`POST /orders/{order_id}/summary` validates `as_of_date` against the
order's real projection history (rejects anything before the earliest
projection date or after today, 422) specifically so a caller can't mint
unbounded cache keys -- and therefore unbounded real Azure OpenAI calls
-- just by varying that one field. That closes the "vary a parameter to
always miss cache" vector, but there is still no request-level rate
limiting (per-IP/per-caller) anywhere in this codebase (checked
`app/main.py`, `app/core/`, `pyproject.toml` -- no `slowapi` or
equivalent is installed) protecting this endpoint's real per-call cost
from an authenticated-but-abusive or simply high-volume caller. Adding
one is deliberately out of scope here -- it's infra-level and belongs
applied consistently across the app if/when other endpoints need it
too, not bolted onto this one route as a one-off. Needed before this
endpoint is exposed in production.

## 9. Tests

```bash
pytest tests/ -v              # everything, ~1 second, in-memory SQLite, no Postgres needed
pytest tests/test_fine_engine.py -v   # just the pure engine
pytest tests/test_migration_parity.py -v # just the Alembic/ORM parity check
```

Before this has ever run against a real Postgres instance in any
environment (see `docs/legacy/root-PROGRESS.md` "Next up") -- if you have
one available, worth doing once:

```bash
export DATABASE_URL="postgresql+psycopg://<real-connection-string>"
alembic upgrade head
python scripts/seed_master_data.py   # (with uvicorn running against the same DATABASE_URL)
python scripts/demo_daily_simulation.py
```

and diff the printed numbers against `data/samples/mars_fines_mock_seed_data.sql`
-- they should match exactly, same as the SQLite verification already
documented in `docs/legacy/root-PROGRESS.md`.

## 10. Linting and formatting

```bash
ruff check app/ scripts/ tests/ alembic/
ruff format app/ scripts/ tests/ alembic/
```

Both must be clean before a change is done -- see
`docs/legacy/root-CLAUDE.md` "Python coding standards."

## 11. Common tasks, quick reference

| I want to... | Run |
|---|---|
| Start completely fresh (drop and recreate schema) | `alembic downgrade base && alembic upgrade head` |
| Reset the four demo orders to their initial state | Re-run `alembic downgrade base && alembic upgrade head`, then step 5 again |
| Check what rules a retailer has | `curl http://127.0.0.1:8000/api/v1/fine-rules?retailer_id=RET-WMT` |
| See an order's full projection trend | `curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/projections` |
| Run one order for a backfilled date | `python scripts/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05` |
| Run projection + summary together, one call | `curl -X POST .../orders/WMT-100234/run -d '{}'` (or `run_projection_cli.py --with-summary`) |
| See the whole system, end to end, in one command | `python scripts/run_end_to_end_demo.py` |
| Get an LLM summary of why an order's fine is what it is | `python scripts/demo_fine_summary.py --order-id WMT-100234` |
| Add a new violation type | Update `SHORTAGE_VIOLATION_TYPES`/`DELAY_VIOLATION_TYPES` in `app/services/fine_projection/models.py`, `docs/FINE_ENGINE.md`, and the client-facing `.docx` together (`docs/legacy/root-CLAUDE.md` "Conventions") |
| Add a DB column | `app/models/*.py` + `alembic revision --autogenerate` + `docs/mars_fines_projection_schema.sql` + confirm `tests/test_migration_parity.py` still passes |

## 12. Troubleshooting

- **`sqlalchemy.exc.OperationalError` / `relation "fact_order" does not
  exist`**: migrations haven't been applied against the `DATABASE_URL`
  the app/script is actually using. Run `alembic upgrade head` with that
  exact `DATABASE_URL` exported first. Easy to hit if a shell without
  `.env` loaded runs a script separately from the one that started
  `uvicorn` -- each process reads its own environment independently.
- **`alembic upgrade head` fails with `relation "dim_retailer" already
  exists` on a database that is clearly already migrated**: it's still on
  the old, pre-squash migration chain (revision `0002`-`0011`) -- see step
  3 "Migration history was squashed to a single `5589e602eefa`" for the drop/
  recreate steps; there is no forward path onto the new `5589e602eefa`.
- **`422` from `POST /projections/run`**: the order's retailer has no
  active fine rules yet. Seed master data (step 5) or add a rule
  (step 7) first.
- **`404` from `POST /projections/run`**: the `order_id` doesn't exist,
  or (for `all_open`) there are no `OPEN` orders at all.
  `GET /orders?order_status=OPEN` to check.
- **`POST /admin/simulate-daily-run` returns `500` / `IntegrityError` /
  `duplicate key`**: fixed -- if you still see this, you're on an older
  build. Update; the fact-writing methods are idempotent on their
  natural key now (see `docs/FINE_ENGINE.md` changelog).
- **Calling `simulate-daily-run` more than once gives a different
  AMZ-778501 number for Aug 11 the second time onward** (WMT-100234,
  WMT-100511, and AMZ-780112 are unaffected): expected, not a bug -- see
  `docs/FINE_ENGINE.md` "Open items" and
  `tests/test_known_limitations.py`. AMZ-778501 and AMZ-780112 share a
  production line with contradictory scripted statuses on their
  overlapping dates; only the *first* `simulate-daily-run` call is
  guaranteed to match the published table for AMZ-778501 -- every call
  after that settles onto a different, but internally consistent,
  number, because AMZ-780112's facts from call 1 are visible the whole
  time AMZ-778501 gets re-projected in call 2 onward. Reset with
  `alembic downgrade base && alembic upgrade head` before re-seeding if
  you need the original numbers back.
- **`StarletteDeprecationWarning` about `httpx`/`httpx2` in test
  output**: known, not yet acted on -- see `docs/legacy/root-PROGRESS.md`
  "Verification status."
- **Port already in use on `uvicorn --reload`**: `uvicorn app.main:app --reload --port 8001`,
  and point scripts at it with `--base-url http://127.0.0.1:8001/api/v1`.
- **`AzureOpenAIConfigError` / the polled job never leaves `FAILED`**:
  one or more of `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`,
  `AZURE_OPENAI_DEPLOYMENT_NAME` isn't set -- see step 8. Double-check
  the deployment var has the `_NAME` suffix; `AZURE_OPENAI_DEPLOYMENT`
  (no suffix) is silently ignored. Since generation is now a background
  job, this surfaces as a `FAILED` status on `GET .../summary`, not
  as an immediate error from the `POST`.
- **`GET /orders/{id}/summary` reports `status: "FAILED"`**: the
  model didn't return valid structured output within the bounded
  tool-calling loop (`ToolLoopExhaustedError`) -- an Azure OpenAI-side
  issue (bad deployment, model overloaded, etc.), not a client input
  error. `POST` again (or with `force_regenerate: true`), or check the
  deployment in the Azure portal.
