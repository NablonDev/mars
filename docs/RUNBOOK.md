# Runbook

Every way to set up, run, seed, exercise, and troubleshoot this service.
For *how the code is laid out*, see `docs/architecture/folder-structure.md`
and `./CLAUDE.local.md` (the current working instructions file); for
endpoint-by-endpoint reference, see `docs/API.md`.
This file is about running it.

**Every curl example below needs `-H "X-Internal-Api-Key: $INTERNAL_API_KEY"`
except `/api/v1/health`.** It's omitted from most of the commands in this file for
readability -- see `docs/API.md` "Authentication" for the full contract
(missing/wrong key -> `401`, generic body, nothing echoed back).

**Sections 2 onward cover the API and the single-order paths.** For the
batch job queue -- every OPEN order, scheduled and run as a group -- see
`docs/DEPLOYMENT.md`:

- **The scheduled daily run is `scripts/ops/run_daily_batch.py`**, executed
  as the Azure Container Apps Job on a nightly cron (§6.6). It both
  enqueues today's work *and* drains it in the same call -- nothing else
  needs to run.
- **`POST /api/v1/batches/run` is not a substitute for the schedule.**
  Under the default `postgres` backend it only enqueues; nothing processes
  those rows until a drain happens (the same script, or the nightly job).
  It's for triggering a run on demand from outside, not for scheduling
  (§6.7). Endpoint reference: `docs/API.md` "Batches".
- Full local walkthrough (enqueue, watch, drain, inspect a stuck queue),
  every `JOB_QUEUE_*`/`SERVICE_BUS_*` config variable, and the Azure build
  sheet: `docs/DEPLOYMENT.md` §3, §4, §6.

## 1. CMIR / PO Validation operations

Operational reference for the CMIR Resolution Agent and PO Validation Agent,
which share the FastAPI process the rest of this runbook covers but add their
own deployed components and failure modes.

### Deployed components

Three independently-running processes, sharing the same Postgres database (the `cmir`
schema):

1. **FastAPI app** (`app.main:app`, same process as the fines API below) -- the only
   process that runs LangGraph. Handles reviewer HTTP traffic and
   `/api/v1/internal/process-email` (the queue-consumer's forwarding target).
2. **Azure Function** (`function_app.py`, timer-triggered every minute) -- claims new
   `email_events` rows and enqueues them to Azure Service Bus. Does **not** run any
   workflow logic itself; trigger definitions live in `azure_functions/` as Blueprints
   registered onto the root `FunctionApp()`.
3. **Service Bus consumer** (`app/workers/cmir_service_bus_consumer.py`, run via `python -m
   app.workers.cmir_service_bus_consumer`) -- a standalone long-running listener. Deserializes
   each queue message and forwards it over HTTP to the FastAPI process; never touches
   the graph or Postgres directly.

All LangGraph execution -- queue-driven or reviewer-driven -- funnels through the one
FastAPI process, keeping checkpoint state centralized.

Run the Service Bus consumer separately when testing the async queue path:
```bash
python -m app.workers.cmir_service_bus_consumer
```

### Required configuration

Loaded once into `app/core/config.py::Settings` (pydantic-settings), same as the fines
config below. Minimum required beyond `DATABASE_URL`: `EMAIL_USERNAME`/
`EMAIL_PASSWORD`/`IMAP_SERVER`, `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`/
`AZURE_OPENAI_DEPLOYMENT_NAME`. Service Bus needs
`SERVICE_BUS_FULLY_QUALIFIED_NAMESPACE`/`SERVICE_BUS_CONNECTION_STRING` for anything
beyond local defaults. **Never commit `.env` or `local.settings.json`** -- rotate any
credential that leaks outside a secrets manager.

### Troubleshooting

#### Thread update/decision returns `THREAD_STALE` (409)
Another reviewer (or the system) updated the thread after the client last fetched it.
Fetch `GET /threads/{thread_id}/stage` or `.../snapshot` for the current
`updated_at` and retry with that value as `expected_updated_at`.

#### Thread action returns `THREAD_NOT_WAITING` (409)
The thread isn't paused at the stage that API expects. Check
`workflow_threads.status`/`stage` -- missing-fields APIs require
`waiting_missing_fields`; update/decision APIs require `waiting_approval`; PO
Validation's resume APIs require `waiting_manual_cmir_entry` /
`waiting_qty_mismatch_decision` respectively.

#### Decision returns `CMIR_VERSION_CONFLICT` (409)
Another thread's approval already superseded the active `cmir_records` row for this
customer/material while this thread was pending. The thread closes to
`COMPLETED_CONFLICT`/`completed_conflict` -- it does **not** reopen for retry
automatically. Fetch `GET /threads/{thread_id}/snapshot` for the thread that actually
won, or start a new one, against the current record.

#### PO Validation resume returns `MATERIAL_NOT_FOUND` (422)
The reviewer-submitted/chosen SAP material number has no `material_master` row for
that plant. This is checked *before* the graph is touched -- nothing was written to
`po_lines`/`po_line_errors` for this attempt. Confirm the material/plant combination
against the SAP mirror sync, or ask the reviewer to pick a different material.

#### Gmail ingest returns no emails
Check, in order: `filters.subject_contains`, `filters.unread_only`, the Gmail app
password (not the normal account password), IMAP enabled on the account,
`EmailConfig.lookback_days`, and whether the target emails are actually unread when
`unread_only=true`.

#### A thread is stuck at `FAILED` with `current_node: persist_email`
The graph raised before `email_id`/the `workflow_threads` row could be created
(`CMIRRunService._process_email_thread`'s exception path). Check `agent_runs.error`
for the underlying exception message -- usually a Postgres connectivity issue or a
constraint violation on `email_events`/`cmir_records`.

#### Service Bus consumer keeps abandoning messages
`app/workers/cmir_service_bus_consumer.py::_process_message` abandons (rather than
completes) any message where the HTTP forward to `/internal/process-email` raises --
check the FastAPI process's logs for the actual failure, not the consumer's. The
consumer retries its receive loop with a 5s backoff on connection-level errors.

### Useful debug queries

Reviewer queue backlog:
```sql
SELECT thread_id, stage, status, updated_at
FROM cmir.workflow_threads
WHERE status NOT LIKE 'completed%'
ORDER BY updated_at DESC;
```

Open pending actions older than expected (possible stuck reviews):
```sql
SELECT thread_id, interrupt_type, created_at
FROM cmir.pending_human_actions
WHERE status = 'open'
ORDER BY created_at ASC;
```

Per-node timing/failures for one run:
```sql
SELECT node_name, status, duration_ms, error
FROM cmir.agent_traces
WHERE run_id = :run_id
ORDER BY started_at ASC;
```

Current CMIR mapping for one customer/material (post-SCD2):
```sql
SELECT * FROM cmir.cmir_records
WHERE customer_identity_key = UPPER(REGEXP_REPLACE(:customer_identity, '[^A-Za-z0-9]', '', 'g'))
  AND target_customer_material_ref_key = UPPER(REGEXP_REPLACE(:material_ref, '[^A-Za-z0-9]', '', 'g'))
  AND is_current;
```

### Deployment notes

- `function_app.py`, `host.json`, `local.settings.json` must stay at the repo root --
  Azure Functions Core Tools and the Functions runtime discover them there by
  convention, not via configuration.
- `requirements.txt` (not `pyproject.toml`/`uv.lock`) is what Azure Functions' Python
  deployment model builds from -- keep it regenerated (`uv export`) after any dependency
  change, or the Function App deployment silently uses stale versions.
- Already-deployed databases on either project's old migration chain cannot be moved
  forward by Alembic itself -- `alembic upgrade head` aborts before any DDL. One whose
  contents are worth keeping is repaired in place, rows intact, by
  `scripts/ops/repair_pre_squash_db.py`; see "A database stranded on the pre-squash
  chain" under "4. Database: migrate" below. Dropping and recreating is now the
  fallback, not the only option. This supersedes the old CMIR-branch guidance to
  `alembic stamp head` against a pre-Alembic database; that guidance no longer
  applies now that both domains share one squashed history.

## 2. Prerequisites

- Python 3.12+
- A Postgres instance. SQLite is enough to create the schema and to run
  the test suite, but a SQLite database built by `alembic upgrade head`
  rejects every write (see "Switching database backends" in step 4), so
  the seeding, projection, and mitigation walkthroughs below all need
  real Postgres.

## 3. First-time setup

```bash
git clone <this repo> && cd mars
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: point DATABASE_URL at your Postgres, and set INTERNAL_API_KEY
# to a real value -- the app refuses to start without one.
```

`app/core/config.py::Settings` reads `DATABASE_URL` from `.env` via
`pydantic-settings`. If `.env` doesn't exist or doesn't set it, the
default is `postgresql+psycopg://postgres:postgres@localhost:5432/mars`
-- a real Postgres URL, not a SQLite fallback, and not the placeholder
`.env.example` carries -- so on a machine with no Postgres running,
always set `.env` (or export `DATABASE_URL` directly) before doing
anything else.

`INTERNAL_API_KEY` has no default at all. `Settings` declares it required
and rejects a blank or too-short value, so a missing key fails app
startup rather than quietly booting something unauthenticated. Every
route except `/api/v1/health` is gated on it.

**Check for a second `INTERNAL_API_KEY` further down `.env` before
debugging a `401`.** Compose parses that file as plain dotenv and keeps
the *last* assignment to a name, then interpolates it into the backend as
`${INTERNAL_API_KEY:?...}` (`docker-compose.yml`). A duplicate definition
lower in the file therefore wins silently: `docker compose up` starts a
backend holding a different key from the one at the top of the file, and
every authenticated call begins returning `401` with nothing in the
response to say why. Grep the whole file, not just its first hit.

## 4. Database: migrate

```bash
alembic upgrade head          # run from the repo root, not app/
```

This creates every table in `docs/DATABASE.md`'s table list. Safe to
re-run (Alembic tracks the applied revision in `alembic_version`).

**Switching database backends.** Changing `DATABASE_URL` and re-running
`alembic upgrade head` builds the schema on either backend -- the DDL
itself compiles on both (`docs/DATABASE.md` "Primary keys" -- the
surrogate `id` type works on both).

```bash
# One-off SQLite database in /tmp, no Postgres needed at all:
export DATABASE_URL="sqlite:////tmp/fines_demo.db"
alembic upgrade head
```

**A SQLite database built that way cannot be written to, though.** Both
migrations give every `created_at`/`updated_at` column
`server_default=sa.text("now()")` -- 67 of them, with no dialect branch.
SQLite accepts that as `DEFAULT (now())` at `CREATE TABLE` time and only
fails on the first insert:

```
sqlite3.OperationalError: unknown function: now()
```

so the recipe above looks like it worked and then breaks the moment
anything writes a row. The test suite never hits this: `tests/conftest.py`
builds its SQLite database with `Base.metadata.create_all()`, and the
ORM's `server_default=func.now()` compiles to `CURRENT_TIMESTAMP` on
SQLite, so the migration path is never exercised there. Treat the SQLite
recipe as a way to inspect schema shape, not to run the app.

**Adding a new migration** after changing `app/models/`:

```bash
alembic revision -m "add whatever" --autogenerate   # needs a live DB connection
```

Then hand-check the generated file against `app/models/` before
committing -- `tests/unit/db/test_migration_parity.py` will fail the
build if they disagree.

### A database stranded on the pre-squash chain

A database last migrated before commit `37f5f56` (the squash down to two
initial migrations) still holds the old chain's head in `alembic_version`,
and that revision file no longer exists:

```
ERROR [alembic.util.messaging] Can't locate revision identified by 'e4b7c391a052'
```

Alembic stops there, before any DDL, so nothing is half-applied. Earlier
versions of this runbook and of `docs/DATABASE.md` said the only way out
was to drop and recreate. That is no longer true:
`scripts/ops/repair_pre_squash_db.py` turns the pre-squash physical schema
into the post-squash one in place, preserving every row, and stamps
`alembic_version` to `43d8ced96170`.

```bash
python scripts/ops/repair_pre_squash_db.py --dry-run   # prints every statement, changes nothing
python scripts/ops/repair_pre_squash_db.py             # the real run
python scripts/ops/repair_pre_squash_db.py --database-url postgresql+psycopg://...
```

It renames the 16 `dim_`/`fact_`-prefixed fines tables along with their
indexes and constraints, syncs every column type and nullability against
`app/models/` (derived from `Base.metadata` at runtime rather than
transcribed, so it cannot drift from the ORM), remaps
`job_item.task_type = 'SUMMARY_REGEN'` to `'PROJECTION_SUMMARY_REGEN'` and
rebuilds that `CHECK`, creates the three tables with no pre-squash
counterpart (`mitigation_input`, `mitigation_option`,
`mitigation_summary`), and stamps the version row. LangGraph's own
`public.checkpoint_*` tables are left alone. The script's module docstring
is the authoritative description.

Worth knowing before running it:

- **`--dry-run` is genuinely read-only.** It opens a plain connection, no
  transaction, prints the statements it would execute, and exits.
- **It refuses anything that isn't the exact pre-squash shape.**
  `alembic_version` must hold exactly `e4b7c391a052`, every old table name
  must be present, and no post-squash name may exist yet -- a partially
  repaired schema is rejected rather than guessed at. A pre-flight pass
  also confirms every value still fits its narrowed column and names the
  offending column and row if not. Exit `2` is a refusal, exit `3` a
  failed data check; neither writes anything.
- **The real run is a single transaction** ending in a `compare_metadata`
  check against `Base.metadata`. Any residual difference raises and rolls
  the whole thing back, so there is no half-repaired outcome to clean up.
- **Stop the backend (and any worker or consumer) first.** The table,
  index and constraint renames and the `ALTER COLUMN ... TYPE` statements
  all take `ACCESS EXCLUSIVE` locks for the length of that transaction; a
  live app either blocks the repair or gets blocked by it.
- It is deliberately not an Alembic revision: a third revision on top of
  `43d8ced96170` would also run against fresh databases and would have to
  detect-and-no-op there, baking a legacy repair into the history
  permanently. `scripts/ops/migrate_legacy_cmir_data.py` is the precedent
  for one-off surgery living in `scripts/ops/`.

### The `cmir` and `fines` schemas

On Postgres, this app's two domains each live in their own schema, not
`public`: CMIR/PO-validation tables (and their agent-observability tables)
in `cmir`, fines-domain tables in `fines`. `public` holds no domain
tables of either -- only Alembic's `alembic_version` and LangGraph's
`checkpoint_*` tables -- see `docs/DATABASE.md`'s "Postgres schema
separation" section.
Each schema name is declared once, in `app/db/base.py::CMIR_SCHEMA`/
`FINES_SCHEMA`, and every model's `__table_args__` binds to one or the
other, so nothing in `app/` needs to qualify a table name by hand beyond
that.

Three consequences worth knowing:

- Alembic's own `alembic_version` bookkeeping table lives in `public`
  (`version_table_schema` in `alembic/env.py`), not in either domain's own
  schema -- it tracks one linear migration history covering both, so it
  belongs in the schema neither domain owns.
- `alembic/env.py::ensure_project_schemas_exist` runs `CREATE SCHEMA IF
  NOT EXISTS for both `cmir` and `fines` before Alembic touches anything
  else, so `alembic upgrade head` bootstraps a brand-new empty database
  with no manual setup.
- SQLite has no schemas at all. `app/db/session.py::apply_sqlite_schema_translation`
  translates both `cmir` and `fines` away at the connection level
  (SQLAlchemy's `schema_translate_map`), which is why the SQLite paths --
  the whole test suite, and the `sqlite:////tmp/fines_demo.db` recipe
  above -- keep working unchanged. Schema translation is not what breaks
  writes on a migration-built SQLite database; the `now()` defaults above
  are.

## 5. Running the API

```bash
uvicorn app.main:app --reload
```

- Interactive docs (try every endpoint from the browser): `http://127.0.0.1:8000/docs`
- Raw OpenAPI schema: `http://127.0.0.1:8000/openapi.json`
- Health check: `curl http://127.0.0.1:8000/api/v1/health`

The app builds its own `Database` from `Settings` at startup
(`app/main.py::create_app`) -- it does **not** run migrations for you.
Step 4 has to happen first, once, against whatever `DATABASE_URL`
you're pointing at.

## 6. Seeding the four worked examples

Two admin endpoints do this over HTTP -- no direct database writes. Both
are idempotent: safe to call repeatedly (e.g. to reset a demo).

```bash
# With the server running (step 5), in another terminal:
python scripts/demo/seed_master_data.py                    # defaults to http://127.0.0.1:8000/api/v1
python scripts/demo/seed_master_data.py --base-url http://localhost:9000/api/v1   # different host/port

python scripts/demo/demo_daily_simulation.py               # replays all 4 scenarios, prints the day-by-day trend
```

Or hit the endpoints directly (shown here with the required header; every
other example in this file omits it for readability -- see the note above):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/seed-master-data \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY"
curl -X POST http://127.0.0.1:8000/api/v1/admin/simulate-daily-run \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY"
```

`seed-master-data` creates 2 retailers, 3 SKUs, 2 locations, 2 carriers,
4 fine rules, and 4 order headers (all `OPEN`). `simulate-daily-run`
walks all four orders through their entire scripted history
(`app/services/seeding/scenario_data_projection.py`), writing each day's facts and running a
projection, then marks every order `DELIVERED`. Running it twice in a
row will re-simulate from scratch and re-mark everything `DELIVERED` --
that's expected, not an error.

## 7. Running projections

**Via the API** (what a real integration would call):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/projections \
  -H "Content-Type: application/json" \
  -d '{"projection_date": "2026-08-05"}'

# Every open order, today:
curl -X POST http://127.0.0.1:8000/api/v1/projections/run \
  -H "Content-Type: application/json" -d '{"all_open": true}'

# Override the retailer's stacking policy for this run only:
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/projections \
  -H "Content-Type: application/json" \
  -d '{"stacking_mode_override": "MAX"}'

curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/projections   # full dated history
curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/exposure      # latest total only

# Projection, then (only if it succeeds) the fine projection summary for the same
# day, in one call -- see docs/API.md "Run projection + summary together":
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/run \
  -H "Content-Type: application/json" -d '{}'
```

**Without a server** (batch/cron-style, straight against the database):

```bash
python scripts/ops/run_projection_cli.py --order-id WMT-100234
python scripts/ops/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05
python scripts/ops/run_projection_cli.py --all-open
python scripts/ops/run_projection_cli.py --all-open --date 2026-08-05 --stacking-mode MAX

# Same projection-then-summary sequencing as POST /orders/{id}/run above,
# for the no-HTTP-server path -- runs the fine projection summary inline (no
# BackgroundTasks needed in a one-shot process) right after each order's
# projection succeeds. Needs AZURE_OPENAI_* configured (step 9):
python scripts/ops/run_projection_cli.py --all-open --with-summary
```

This is the no-HTTP-server path for one order or a backfilled date --
standing up a server just to run a batch job is unnecessary overhead. The
*scheduled* daily run is `scripts/ops/run_daily_batch.py` instead (it
enqueues and drains in one call; see the note at the top of this file and
`docs/DEPLOYMENT.md` §3.5). No scheduler is actually provisioned yet --
`docs/DEPLOYMENT.md` §6.6 is the build sheet for the nightly job, §10
tracks it as outstanding.

### Mitigation options need a projection for the same date first

`POST /orders/{order_id}/mitigation-options` never computes a projection of
its own. `FineMitigationService.run_for_order` loads the order's persisted
`fines.projected_fine` rows for one `projection_date` -- today, in UTC,
when the body omits it -- and raises `NO_PROJECTION_EXISTS` (`422`) if that
day has none. The order is always projections first, mitigation second,
against the same date:

```bash
# 1. Project today, every open order:
curl -X POST http://127.0.0.1:8000/api/v1/projections/run \
  -H "Content-Type: application/json" -d '{"all_open": true}'

# 2. Then rank mitigation actions for one of them:
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/mitigation-options \
  -H "Content-Type: application/json" -d '{}'
```

**A freshly seeded database has projection rows and still fails step 2.**
`simulate-daily-run` replays each order's scripted history and stops there
-- 2026-08-02 to 08-11 for WMT-100234, 08-06 to 08-14 for WMT-100511,
08-03 to 08-13 for AMZ-778501, 08-11 to 08-17 for AMZ-780112
(`app/services/seeding/scenario_data_projection.py`). It is a historical
time series, not a projection for the current date, so
`GET /orders/{id}/projections` looks perfectly healthy while
`POST /orders/{id}/mitigation-options` with an empty body returns `422`.
Either run today's projection first, or ask for a date the history
actually covers:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/mitigation-options \
  -H "Content-Type: application/json" -d '{"projection_date": "2026-08-05"}'
```

**`all_open: true` skips the four demo orders once they have been
simulated.** `simulate-daily-run` marks each one `DELIVERED` after its last
scripted day, and `run_for_all_open` iterates only `order_status = 'OPEN'`
-- so on a fully simulated database the batch form runs nothing and returns
`[]`. Project those orders one at a time with
`POST /orders/{order_id}/projections`, or re-seed without simulating.

`POST /projections/run` is batch-only. `RunProjectionRequest` still carries
an `order_id` field, but the handler rejects any body without
`all_open: true` and points at the per-order route -- a deliberate `422`
redirect, not a validation gap. Its other two fields are `projection_date`
(defaults to today) and `stacking_mode_override` (`SUM` / `MAX`).

Why this is keyed by date rather than computed once and reused: a
projection is that day's snapshot of failure probability, and the inputs
move daily -- days-to-delivery shrinks, confirmations arrive and change the
confirmed quantity, production status changes, a shipment is created and
then actually ships. A ranking of mitigation actions priced against
yesterday's probabilities is the wrong ranking, which is why the engine
reads one specific day's rows instead of "the latest".

`POST /orders/{order_id}/mitigation-run` does mitigation and its LLM
summary in one call, the same way `POST /orders/{order_id}/run` does for
projections -- it too assumes the projection for that date already exists.
`200` when the mitigation summary was already cached for that day, `202`
on a cache miss (poll `GET /orders/{order_id}/mitigation-summary`).
Endpoint reference: `docs/API.md` "Run mitigation options + mitigation
summary together".

**Without a server:** `scripts/ops/run_mitigation_cli.py` mirrors
`run_projection_cli.py` for the mitigation side --
`--order-id`/`--all-open` and `--date` to rank, plus `--with-summary` to
also run the fine mitigation summary inline (no `BackgroundTasks` needed
in a one-shot process) right after each order's ranking succeeds. Same
prerequisite as the API: a projection has to exist for that date first.

## 8. Feeding new facts (not one of the four scripted scenarios)

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

# A rule has to exist for the retailer or /orders/{order_id}/projections returns 422:
curl -X POST http://127.0.0.1:8000/api/v1/fine-rules \
  -H "Content-Type: application/json" \
  -d '{"rule_id": "RULE-TGT-OTIF", "retailer_id": "RET-TGT", "violation_type": "OTIF_LATE",
       "calc_type": "PERCENT_OF_PO", "rate": 0.03}'

# SAP just cut the order:
curl -X POST http://127.0.0.1:8000/api/v1/orders/TGT-1/confirmations \
  -H "Content-Type: application/json" \
  -d '{"confirmation_id": "CONF-TGT-1-01", "confirmed_qty": 450,
       "confirmation_date": "2026-09-03T06:00:00"}'

curl -X POST http://127.0.0.1:8000/api/v1/orders/TGT-1/projections \
  -H "Content-Type: application/json" -d '{}'
```

`POST /orders/{id}/shipments` and `POST /production-schedules` work the
same way -- see `docs/API.md`. Every write is historized where the
schema calls for it (`docs/DATABASE.md` "Historization"), so posting the
same kind of fact again doesn't overwrite the last one, it adds to the
timeline `build_snapshot` reads "as of" a given date from.

## 9. Azure OpenAI configuration (for the fine-projection-summary and fine-mitigation-summary features)

`POST /orders/{order_id}/projection-summary` (see `docs/API.md` "Fine Projection Summaries")
and `POST /orders/{order_id}/mitigation-summary` (see `docs/API.md` "Fine
Mitigation Summaries") are the only things in this codebase that call out
to an LLM. The mitigation one is a full mirror of the projection one --
same bounded tool-calling loop, same cache/background-job mechanics --
just explaining an order's ranked mitigation options instead of its
projection trace (`app/services/fine_mitigation/summary.py`). Everything
else works with no Azure credentials set at all. Generation runs as a
background job for both -- the `POST` itself never calls Azure OpenAI
inline, so missing/bad credentials surface as a `FAILED` status on
`GET /orders/{order_id}/projection-summary` or
`GET /orders/{order_id}/mitigation-summary`, not as a failure of the `POST`
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
`FineProjectionSummaryService.run_generation` (or, on the mitigation
side, `FineMitigationSummaryService.run_generation`) and persisted as a
`FAILED` row rather than propagating into an HTTP response at all. The
real exception is logged server-side
(`app.services.fine_projection.summary` /
`app.services.fine_mitigation.summary`, `logger.exception(...)`); only
the generic, client-safe `"Fine projection summary generation failed
upstream"` (or, for mitigation, `"Fine mitigation summary generation
failed upstream"`) message reaches the row a client can poll, per this
app's message/detail split (`app/core/exceptions.py`).

With those set:

```bash
# Fast, no LLM call inline -- 200 (cache hit) or 202 (job scheduled):
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/projection-summary \
  -H "Content-Type: application/json" -d '{}'

# Poll until status leaves PENDING:
curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/projection-summary

# Force a fresh LLM call even if today's summary is already cached:
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/projection-summary \
  -H "Content-Type: application/json" -d '{"force_regenerate": true}'
```

The mitigation summary is the same three calls against
`/mitigation-summary` instead, with the same `as_of_date`/
`force_regenerate` body fields -- it needs ranked mitigation options to
already exist for that date (`POST /orders/{order_id}/mitigation-options`
first, see step 7's "Mitigation options need a projection for the same
date first"):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/mitigation-summary \
  -H "Content-Type: application/json" -d '{}'

curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/mitigation-summary

curl -X POST http://127.0.0.1:8000/api/v1/orders/WMT-100234/mitigation-summary \
  -H "Content-Type: application/json" -d '{"force_regenerate": true}'
```

Or the scripted equivalent, which looks up each order's actual latest
projection (or, for mitigation, mitigation-options) date first rather
than relying on today happening to fall inside the mock scenarios' Aug
2026 date range:

```bash
python scripts/demo/demo_fine_projection_summary.py                          # every order on file
python scripts/demo/demo_fine_projection_summary.py --order-id WMT-100234     # one order
python scripts/demo/demo_fine_projection_summary.py --order-id WMT-100234 --force-regenerate

python scripts/demo/demo_fine_mitigation_summary.py                          # same idea, for mitigation options
python scripts/demo/demo_fine_mitigation_summary.py --order-id WMT-100234
python scripts/demo/demo_fine_mitigation_summary.py --order-id WMT-100234 --force-regenerate
```

**The full tour, in one command** -- seed, replay all four scenarios,
then explain each order's final number, chained together
(`scripts/demo/run_end_to_end_demo.py`):

```bash
python scripts/demo/run_end_to_end_demo.py                   # needs Azure OpenAI creds for the last stage
python scripts/demo/run_end_to_end_demo.py --skip-fine-projection-summary  # engine-only, no LLM cost, no creds needed
```

Leave the `AZURE_OPENAI_*` vars unset/blank to run every other part of
the app normally -- `Settings` defaults them all to `""`, and the
fine-projection-summary and fine-mitigation-summary endpoints only fail
(`AzureOpenAIConfigError`, surfaced as a clean error, not a stack trace)
the first time either is actually called, not at app startup.

### Known limitation: no request-level rate limiting

`POST /orders/{order_id}/projection-summary` and
`POST /orders/{order_id}/mitigation-summary` both validate `as_of_date`
against the order's real history -- projection dates for the former,
mitigation-options dates for the latter (rejects anything before the
earliest such date or after today, 422) -- specifically so a caller can't
mint unbounded cache keys -- and therefore unbounded real Azure OpenAI
calls -- just by varying that one field. That closes the "vary a
parameter to always miss cache" vector, but there is still no
request-level rate limiting (per-IP/per-caller) anywhere in this codebase
(checked `app/main.py`, `app/core/`, `pyproject.toml` -- no `slowapi` or
equivalent is installed) protecting either endpoint's real per-call cost
from an authenticated-but-abusive or simply high-volume caller. Adding
one is deliberately out of scope here -- it's infra-level and belongs
applied consistently across the app if/when other endpoints need it
too, not bolted onto either route as a one-off. Needed before either
endpoint is exposed in production.

## 10. Tests

```bash
pytest tests/ -v              # everything: 544 passed, 5 skipped, ~65s, in-memory SQLite
pytest tests/unit/services/test_fine_engine.py -v   # just the pure engine
pytest tests/unit/db/test_migration_parity.py -v    # just the Alembic/ORM parity check
```

The suite itself never touches Postgres. Re-running the worked examples
against a real Postgres instance is still worth doing after any change to
the engine or the seeding data:

```bash
export DATABASE_URL="postgresql+psycopg://<real-connection-string>"
alembic upgrade head
python scripts/demo/seed_master_data.py   # (with uvicorn running against the same DATABASE_URL)
python scripts/demo/demo_daily_simulation.py
```

and diff the printed numbers against `data/samples/mars_fines_mock_seed_data.sql`
-- they should match exactly, same as the verification runs logged in
`PROGRESS.local.md`.

## 11. Linting and formatting

```bash
ruff check app/ scripts/ tests/ alembic/
ruff format app/ scripts/ tests/ alembic/
```

Both must be clean before a change is done -- see `./CLAUDE.local.md`
"Engineering Rules."

## 12. Common tasks, quick reference

| I want to... | Run |
|---|---|
| Start completely fresh (drop and recreate schema) | `alembic downgrade base && alembic upgrade head` |
| Reset the four demo orders to their initial state | Re-run `alembic downgrade base && alembic upgrade head`, then step 6 again |
| Check what rules a retailer has | `curl http://127.0.0.1:8000/api/v1/fine-rules?retailer_id=RET-WMT` |
| See an order's full projection trend | `curl http://127.0.0.1:8000/api/v1/orders/WMT-100234/projections` |
| Run one order for a backfilled date | `python scripts/ops/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05` |
| Run projection + summary together, one call | `curl -X POST .../orders/WMT-100234/run -d '{}'` (or `run_projection_cli.py --with-summary`) |
| See the whole system, end to end, in one command | `python scripts/demo/run_end_to_end_demo.py` |
| Get an LLM summary of why an order's fine is what it is | `python scripts/demo/demo_fine_projection_summary.py --order-id WMT-100234` |
| Rank mitigation actions for an order | Project that date first, then `curl -X POST .../orders/WMT-100234/mitigation-options -d '{}'` (§7) |
| Get an LLM summary of an order's ranked mitigation options | `python scripts/demo/demo_fine_mitigation_summary.py --order-id WMT-100234` |
| Rank mitigation actions + summarize them together, one call | `curl -X POST .../orders/WMT-100234/mitigation-run -d '{}'` (or `run_mitigation_cli.py --with-summary`) |
| Repair a database stuck on the old migration chain | `python scripts/ops/repair_pre_squash_db.py --dry-run`, then without the flag (§4) |
| Add a new violation type | Update `SHORTAGE_VIOLATION_TYPES`/`DELAY_VIOLATION_TYPES` in `app/services/fine_projection/types.py` (`./CLAUDE.local.md` "Engineering Rules") |
| Add a DB column | `app/models/*.py` + `alembic revision --autogenerate` + update `docs/DATABASE.md` + confirm `tests/unit/db/test_migration_parity.py` still passes |
| Run the full nightly batch locally | `python scripts/ops/run_daily_batch.py` (see `docs/DEPLOYMENT.md` §3.5) |
| See why the queue looks stuck | `docs/DEPLOYMENT.md` §3.7 — the SQL to run and what each status means |
| Deploy to Azure | `docs/DEPLOYMENT.md` §6 |

## 13. Troubleshooting

- **`sqlalchemy.exc.OperationalError` / `relation "sales_order" does not
  exist`**: migrations haven't been applied against the `DATABASE_URL`
  the app/script is actually using. Run `alembic upgrade head` with that
  exact `DATABASE_URL` exported first. Easy to hit if a shell without
  `.env` loaded runs a script separately from the one that started
  `uvicorn` -- each process reads its own environment independently.
- **`alembic upgrade head` fails with `Can't locate revision identified
  by 'e4b7c391a052'`**: the database is still on the pre-squash migration
  chain, whose head revision file the squash deleted. Alembic aborts
  before any DDL, so nothing is half-applied. Repair it in place with
  `scripts/ops/repair_pre_squash_db.py` (`--dry-run` first) -- see "A
  database stranded on the pre-squash chain" in step 4. Dropping and
  recreating is still fine if the rows don't matter.
- **`401` `{"detail": "Not authenticated"}` on every call**: the header
  name is exactly `X-Internal-Api-Key`. A **missing** header and a
  **wrong** key produce byte-identical responses, so the body tells you
  nothing about which of the two you hit: `require_internal_api_key`
  (`app/api/dependencies.py`) raises `HTTPException(401, detail="Not
  authenticated")`, and `register_exception_handlers`
  (`app/core/exceptions.py`) registers handlers for `AppError`,
  `ServiceError` and bare `Exception` but none for `HTTPException` -- so
  Starlette's default handler answers, and the reply is a flat
  `{"detail": ...}` rather than this project's usual
  `{"error": {"code": ..., "message": ...}}` envelope. Debug it from both
  ends instead: confirm the header is actually being sent, then confirm
  its value matches the `INTERNAL_API_KEY` the *running process* loaded --
  not merely the first one in `.env` (step 3, on duplicate definitions).
  `curl http://127.0.0.1:8000/api/v1/health` needs no key and separates
  "is the app up" from "is my key right".
- **`404` from `/health`**: the health route sits under the API prefix
  like everything else -- it's `GET /api/v1/health`. It is also the one
  route that needs no API key.
- **`422` `NO_PROJECTION_EXISTS` from `POST
  /orders/{order_id}/mitigation-options`**: no projection row exists for
  that order on that date. Mitigation reads a persisted projection and
  never computes one, so run `POST /orders/{order_id}/projections` for the
  same date first. On a freshly seeded database this happens even though
  the order has plenty of projection rows -- the seeded history stops in
  mid-August 2026 and the endpoint defaults to today. See "Mitigation
  options need a projection for the same date first" in step 7.
- **`422` `NO_MITIGATION_OPTIONS_EXIST` from `POST`/`GET
  /orders/{order_id}/mitigation-summary` (or `mitigation-run`)**: no
  ranked mitigation options exist for that order yet -- run `POST
  /orders/{order_id}/mitigation-options` (which itself needs a projection
  first, see above) before asking for a summary of them.
- **`404` `NO_MITIGATION_SUMMARY_JOB_EXISTS` from `GET
  /orders/{order_id}/mitigation-summary`**: no job was ever `POST`ed for
  that exact `(order_id, as_of_date, prompt_version)` key -- `POST` it
  first, then poll. Same shape as `NO_PROJECTION_SUMMARY_JOB_EXISTS` for
  the projection-summary side.
- **`422` from `POST /orders/{order_id}/projections` (or `POST
  /projections/run` with `all_open: true`)**: the order's retailer has
  no active fine rules yet. Seed master data (step 6) or add a rule
  (step 8) first.
- **`404` from `POST /orders/{order_id}/projections`**: the `order_id`
  doesn't exist. For `POST /projections/run` (`all_open: true`), a `404`
  isn't order-specific -- check `GET /orders?order_status=OPEN` if
  nothing ran.
- **`POST /admin/simulate-daily-run` returns `500` / `IntegrityError` /
  `duplicate key`**: fixed -- if you still see this, you're on an older
  build. Update; the fact-writing methods are idempotent on their
  natural key now.
- **Calling `simulate-daily-run` more than once gives a different
  AMZ-778501 number for Aug 11 the second time onward** (WMT-100234,
  WMT-100511, and AMZ-780112 are unaffected): expected, not a bug -- see
  `tests/unit/services/test_known_limitations.py`. AMZ-778501 and
  AMZ-780112 share a
  production line with contradictory scripted statuses on their
  overlapping dates; only the *first* `simulate-daily-run` call is
  guaranteed to match the published table for AMZ-778501 -- every call
  after that settles onto a different, but internally consistent,
  number, because AMZ-780112's facts from call 1 are visible the whole
  time AMZ-778501 gets re-projected in call 2 onward. Reset with
  `alembic downgrade base && alembic upgrade head` before re-seeding if
  you need the original numbers back.
- **Port already in use on `uvicorn --reload`**: `uvicorn app.main:app --reload --port 8001`,
  and point scripts at it with `--base-url http://127.0.0.1:8001/api/v1`.
- **`AzureOpenAIConfigError` / the polled job never leaves `FAILED`**:
  one or more of `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`,
  `AZURE_OPENAI_DEPLOYMENT_NAME` isn't set -- see step 9. Double-check
  the deployment var has the `_NAME` suffix; `AZURE_OPENAI_DEPLOYMENT`
  (no suffix) is silently ignored. Since generation is now a background
  job, this surfaces as a `FAILED` status on `GET .../projection-summary`
  or `GET .../mitigation-summary` (both share the same Azure OpenAI
  config), not as an immediate error from the `POST`.
- **`GET /orders/{id}/projection-summary` or `GET
  /orders/{id}/mitigation-summary` reports `status: "FAILED"`**: the
  model didn't return valid structured output within the bounded
  tool-calling loop (`ToolLoopExhaustedError`, coded
  `FINE_PROJECTION_SUMMARY_UPSTREAM_FAILED` /
  `FINE_MITIGATION_SUMMARY_UPSTREAM_FAILED` respectively) -- an Azure
  OpenAI-side issue (bad deployment, model overloaded, etc.), not a
  client input error. `POST` again (or with `force_regenerate: true`), or
  check the deployment in the Azure portal.
