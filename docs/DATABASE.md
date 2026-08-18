# Database

Conceptual/canonical schema (fines domain only): `docs/mars_fines_projection_schema.sql`.
Runnable ORM for both domains: `app/models/`. Migrations: `alembic/` (repo root, run
with `alembic upgrade head`). Sample data: `data/samples/mars_fines_mock_seed_data.sql`.

## Postgres schema separation (`cmir` / `fines` / `public`)

This one FastAPI app hosts two domains against one physical Postgres
database, split across separate **schemas**:

- **`cmir`** -- every table the CMIR/PO-validation domain owns, declared in
  `app/db/base.py::CMIR_SCHEMA`. This currently includes CMIR's own
  domain tables (`cmir_records`, `email_events`, `email_action_log`,
  `po_lines`, `material_master`, `po_line_errors`, `workflow_threads`)
  *and* its per-run observability/HITL tables (`agent_runs`,
  `agent_traces`, `hitl_actions`, `pending_human_actions`) -- see "CMIR /
  PO Validation tables" below for the per-table reference.
- **`fines`** -- every table the Projected Fines domain owns (all 16, see
  "Tables" below), plus the shared Alembic bookkeeping table
  (`fines.alembic_version` -- one linear migration history covers both
  schemas, so it doesn't matter which one anchors the version table).
  Declared in `app/db/base.py::FINES_SCHEMA`.
- **`public`** -- currently unused by either domain. A future split that
  moves the genuinely cross-domain agent-observability tables
  (`agent_runs`, `agent_traces`, `hitl_actions`) into `public` so a third,
  unrelated agent project could consume them without depending on `cmir`
  internals is a real idea that came up while merging the two domains
  together, but was explicitly deferred -- not implemented in this pass.
  If/when it happens, `workflow_threads` and `pending_human_actions` stay
  in `cmir` (they FK into cmir-private tables like
  `email_events`/`po_lines`), so revisit this split table-by-table rather
  than moving the whole set at once.

Every FK string across both domains is schema-qualified
(`ForeignKey(f"{CMIR_SCHEMA}.agent_runs.id")`, matching the existing
`FINES_SCHEMA` convention) -- nothing relies on the connection's default
`search_path`.

On SQLite (the whole test suite, plus the `sqlite:////tmp/...` local-dev
recipe), schemas don't exist at all -- `app/db/session.py` and
`alembic/env.py` both apply SQLAlchemy's `schema_translate_map` at the
connection level to translate `fines` and `cmir` away, so nothing in
`app/` or `tests/` needs to know schemas are involved.

### Migration history

One linear chain, two migrations:

| Revision | File | What it adds |
|---|---|---|
| (see `alembic/versions/`) | `8209afe73fa4_cmir_initial_schema.py`, `down_revision=5589e602eefa` | Every `cmir`-schema table, replacing the CMIR branch's old standalone `0001`-`0005` raw-SQL chain |
| `5589e602eefa` | `alembic/versions/5589e602eefa_initial_schema.py` | Every `fines`-schema table |

Both the old fines `0001`-`0011` chain and the CMIR branch's own `0001`-`0005`
chain were squashed rather than reconciled migration-by-migration -- neither
had shipped to an environment carrying real data (demo/seed rows only), so
there was nothing an incremental history needed to preserve, and every
residual naming/type issue either chain had accumulated (stale
`dim_penalty_rule_*` constraint names, `agent_trace` -> `agent_traces`,
`agent_runs.id` `SERIAL` -> UUIDv7) is gone with it. A database still on
either old chain has no forward path onto the new one -- see
`docs/RUNBOOK.md`'s "Database: migrate" section for the drop/recreate steps.

## CMIR / PO Validation tables

All of the following live in the `cmir` schema (`app/models/cmir.py`,
`app/models/email.py`, `app/models/observability.py`,
`app/models/po_validation.py`).

### `email_events` (`EmailEventORM`)
One row per inbound email, doubling as the Service Bus queue's work item.

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `sender`, `subject`, `raw_content` | Raw email content |
| `source_message_id`, `source_imap_id` | Duplicate-detection keys |
| `extracted_json`, `missing_fields`, `status` | Latest CMIR extraction result |
| `queue_status` | `new` -> `enqueueing` -> `queued` -> `processing` -> `processed` / `failed` |
| `queue_message_id`, `queue_delivery_count`, `queue_error` | Service Bus delivery bookkeeping |

### `email_action_log` (`EmailActionLogORM`)
Append-only audit log of email-level workflow events. Write-only from the
app's perspective -- no read path in the API today.

### `cmir_records` (`CMIRRecordORM`)
SCD2 history of approved CMIR mappings -- **not** an append-only log. Exactly
one `is_current = TRUE` row per `(customer_identity_key,
target_customer_material_ref_key)`, enforced by a partial unique index (see
`app/repositories/cmir.py::PostgresCMIRRepository.supersede_and_insert`,
which retires the old row and inserts the new one in one transaction,
catching the index violation as `CMIRVersionConflict` on a lost race).
Written by both agents (CMIR's `persist_cmir` node; PO Validation's
`create_cmir_record` node for manual mappings); read by PO Validation's
`validate_against_cmir` node -- shared table, shared repository class.

### `agent_runs` (`AgentRunORM`)
One row per independent agent execution (one email, or one PO line). `id`
is a UUIDv7 surrogate key (converted from the CMIR branch's original
`SERIAL` int during the fines/cmir merge, to match the house convention
every other surrogate-keyed table in this repo already uses). `run_type`
(`email_ingest` vs `PO_VALIDATION`) and the nullable `email_id`/`po_line_id`
columns distinguish which agent owns a given run. `batch_id` groups every
run created by one ingest request.

### `workflow_threads` (`WorkflowThreadORM`)
The reviewer-facing thread -- **the only identifier reviewer/UI actions may
key on** (see the README's Identity Rule). Shared by both agents,
distinguished by whether `email_id` or `po_line_id` is populated.
`agent_run_id` is a UUID FK into `agent_runs.id`.

### `pending_human_actions` (`PendingHumanActionORM`)
Open/completed human-review interrupts, one per thread at a time.
`agent_run_id` is a UUID FK into `agent_runs.id`; `thread_id` FKs into
`workflow_threads.thread_id` (both now within the same `cmir` schema).

### `hitl_actions` (`HITLActionORM`)
Audit trail of every reviewer answer/decision, written transactionally
alongside the `pending_human_actions` transition by
`PostgresHITLStateRepository.apply_human_action`. `run_id` is a UUID FK
into `agent_runs.id`.

### `agent_traces` (`AgentTraceORM`)
One row per LangGraph node execution (`completed` / `paused` / `failed`),
written by the `traced()` decorator (`app/core/tracing.py`) wrapping every
node in both graphs. Operational/debugging surface only. Renamed from
`agent_trace` (singular) during the fines/cmir merge; `run_id` is a UUID
FK into `agent_runs.id`.

### `po_lines` (`PoLineORM`)
One row per PO line submitted for validation.

### `material_master` (`MaterialMasterORM`)
Local mirror of SAP MARC fields, keyed by `(sap_material_number, plant)`.
Populated by an out-of-scope external sync process.

### `po_line_errors` (`PoLineErrorORM`)
System/lookup failures on a PO line -- distinct from `hitl_actions` (human
decisions). `agent_run_id` is a UUID FK into `agent_runs.id`.

### LangGraph checkpoint tables
Created and owned entirely by `PostgresSaver.setup()`
(`app/core/container.py`) -- never created via Alembic and never
queried/written directly by application code. Both agents' graphs share one
`PostgresSaver` instance; checkpoint `thread_id`s are namespaced
(`thread_po_...` for PO Validation) so the two graphs' checkpoints never
collide.

### Cross-agent sharing summary

| Shared by both agents | CMIR-agent-only | PO-Validation-only |
|---|---|---|
| `agent_runs`, `workflow_threads`, `pending_human_actions`, `hitl_actions`, `agent_traces`, `cmir_records` | `email_events`, `email_action_log` | `po_lines`, `material_master`, `po_line_errors` |

## Primary keys

Every fines-domain table, plus `agent_runs` and everything that FKs into it
(`agent_traces`, `hitl_actions`, `pending_human_actions`, `workflow_threads`,
`po_line_errors`), has a surrogate `id` (UUIDv7, time-ordered) as its actual
primary key. Business identifiers (`order_id`, `retailer_id`, `rule_id`,
`sku_id`, etc.) are separate `unique=True` columns: they're what the API,
tests, and every worked example already address resources by
(`/orders/WMT-100234`, not `/orders/<uuid>`), and foreign keys reference
them directly rather than the surrogate `id` where a business key exists.
A handful of CMIR/PO-validation tables (`cmir_records`, `email_action_log`,
`hitl_actions`, `pending_human_actions`, `agent_traces` themselves) still
use a plain integer PK -- untouched by this convention, since they have no
business key and nothing FKs into them from outside their own domain.

`app/db/base.py::generate_uuid7()` (a Python-side default on every
UUID-surrogate `id` column) is the id-generation path the application
itself uses. The initial fines migration
(`alembic/versions/5589e602eefa_initial_schema.py`) additionally sets
`server_default=uuidv7()` (Postgres's own builtin, 18+) directly on every
`fines`-schema table's `id` column via raw DDL, purely as a migration-level
safety net for a hypothetical future non-ORM writer that inserts without
supplying `id`. Version-gated to Postgres 18+, and a no-op on SQLite.

One exception: `fact_projected_fine` has no single natural key -- its
business identity is the triple `(order_id, rule_id, projection_date)`,
enforced as a `UniqueConstraint`, which is what
`ProjectionRepository.save_result` upserts against.

`fact_fine_summary.prompt_version` is no longer a bare string: alongside
a new `agent_id` column (no default -- always supplied by
`FineSummaryService`), it's a composite FK into
`dim_prompt_version(agent_id, prompt_version)` -- see "Agent/prompt
registry" below.

Similarly, `fact_fine_summary`'s business identity is the
triple `(order_id, as_of_date, prompt_version)` -- a prompt-version bump
is a deliberate content change, so a summary generated under an old
prompt must never be served in place of one generated under a new one.
Generation is a background job (`FineSummaryService.get_or_schedule`/
`run_generation`, see app/api/v1/fine_summaries.py): a row moves through
`status` PENDING -> READY or PENDING -> FAILED, and a fresh request
against an already-existing key (a cache miss re-request, or
`force_regenerate`) overwrites that row in place
(`FineSummaryRepository.create_pending`) rather than inserting a
second one -- this table holds the *latest* summary job per key, not a
full history of every attempt.

## Historization

`fact_order_confirmation`, `fact_production_schedule`, and
`fact_shipment` are all append-only: every write is a new row, never an
update to an existing one. `OrderRepository.build_snapshot` always
selects the latest row "as of" the requested projection date from each,
which is what makes `--date` backfills (via `scripts/ops/run_projection_cli.py`
or `POST /projections/run`) reflect what was actually known on that day,
not today's current state. See `docs/FINE_ENGINE.md` changelog for
why `fact_shipment` specifically needed fixing to follow this pattern.

`fact_production_schedule` is keyed by `(sku_id, location_id)`, not
`order_id` -- a production line serves whichever orders draw on it. Two
orders that share a line will see each other's status updates; see
`tests/test_known_limitations.py` for the one place in the mock data
where that's a real, accepted wrinkle rather than a bug.

## Agent/prompt registry

`dim_agent` and `dim_prompt_version` (part of the fines migration) give
`fact_fine_summary.prompt_version` a real relationship to the agent that
generated it and the module the prompt text actually lives in, in place
of a bare unchecked string. Deliberately generic (agent_name +
prompt_version, not fine-summary-specific) and deliberately kept in the
`fines` schema, separate from the CMIR domain's own `agent_runs` (a
different concept -- one execution, not a named/versioned agent
definition). `dim_agent.source` (`"fines"` or `"cmir"`) records which
domain actually owns/runs a given agent, in case these two registries are
ever unified later.

`dim_prompt_version.agent_id`/`fact_fine_summary.agent_id` reference
`dim_agent.id` (the surrogate UUID), not `agent_name` -- unlike most other
FKs in this codebase (see "Primary keys" above), a deliberate choice for
this specific pair: an agent's identity here is structural/never-renamed,
not a business key callers address resources by the way `order_id`/
`retailer_id` are.

Rows are registered idempotently by `FineSummaryService` itself
(`PromptRegistryRepository.ensure_registered`), called right before the
service writes a `fact_fine_summary` row under that key -- not at app
startup, and not seeded via a migration or a script. This keeps
registration tied to the operation that actually needs it (no separate
boot-time side effect unrelated to any specific request) while still
guaranteeing the FK is always satisfiable before the write that depends
on it. Bumping the fine-summary prompt to a new version (a new
`app/agents/prompts/fine_summary/vN.py`) self-registers on the next call;
nothing has to be remembered or run by hand.

## Batch job queue (`job_run` / `job_item`)

Two tables backing the batch dispatch/claim pipeline for fine projections
and summary regeneration, added alongside two new nullable columns on
`fact_fine_summary` (`content_fingerprint`, `source_as_of_date` -- see the
"Client-facing audit trail" note in `docs/mars_fines_projection_schema.sql`
for what each supports).

`job_run` is one row per batch trigger (`SCHEDULED_DAILY` / `MANUAL_BATCH`
/ `ON_DEMAND`). It deliberately has **no status column** -- concurrent
workers update `job_item` rows continuously while a run is in flight, and
a status column here would mean every one of them also has to lock this
single shared row, a hot-lock bottleneck for no real benefit. A run's
status is *derived* at read time by grouping `job_item` rows by
`job_run_id`/`status` (`JobQueueRepository.get_run_summary`). Do not add
one back later without re-deriving why this was avoided.

`job_item` is the work ledger -- one row per (order, projection_date,
task_type) unit of work. `status` moves `PENDING -> RUNNING -> SUCCEEDED`,
or `PENDING -> RUNNING -> PENDING` (retry, `available_at` pushed into the
future) `-> ... -> DEAD`. There is deliberately **no resting `FAILED`
state** -- see `app/models/job_queue.py::JobItem`'s docstring. `id` is a
UUIDv7, which doubles as a FIFO tiebreaker when the claim query orders by
`(available_at, id)`. `attempt_count` increments as part of the claim
itself (not after dispatch), so a crash mid-attempt still consumes retry
budget rather than retrying forever.

### The partial unique index is migration-only, not on the ORM model

`job_item` needs `UNIQUE (order_id, projection_date, task_type) WHERE
status IN ('PENDING', 'RUNNING')` (named `uq_job_item_inflight`) to stop
duplicate in-flight work for the same order/date/task while still
allowing many terminal rows across days and retries. This is declared
**only** as raw DDL in the migration
(`alembic/versions/ff84c023d5e9_job_queue_and_fine_summary_fingerprint_.py`),
never as an `Index(..., postgresql_where=...)` on `JobItem` itself:
SQLAlchemy silently drops `postgresql_where=` on SQLite (the dialect the
whole test suite runs against, see `tests/conftest.py`), which would leave
a *full*, non-partial, unique index in the SQLite test database and
wrongly reject a legitimate second terminal (`SUCCEEDED`/`DEAD`) row for
the same key.

Two direct consequences worth knowing about if you touch this table:

- **`tests/test_migration_parity.py` cannot catch a divergence here
  either way** -- it only diffs table/column names between the migration
  and `Base.metadata.create_all()`, never indexes. This index's actual
  correctness (does it exist, does it reject/permit the right rows) is
  verified only against a real Postgres, in
  `tests/integration/test_job_queue_postgres.py` -- which skips cleanly,
  not silently, when no reachable `DATABASE_URL` is configured.
- `Base.metadata.create_all()` (what `tests/conftest.py`'s SQLite fixture
  uses) never creates this index either, so
  `JobQueueRepository.enqueue`/`enqueue_many` cannot lean on a DB-level
  constraint for idempotency in unit tests -- they fall back to an
  application-level pre-check query instead. See the repository's own
  docstrings for the exact dialect branching.

The Alembic migration itself still has to run cleanly on both dialects
(the SQLite half of that same migration is what `test_migration_parity.py`
actually exercises), so the raw `CREATE UNIQUE INDEX ... WHERE ...`
statement is dialect-branched inside the migration: schema-qualified
(`fines.job_item`) on Postgres, unqualified on SQLite -- because
`apply_sqlite_schema_translation`'s `schema_translate_map` only rewrites
schema-qualified names inside SQLAlchemy-compiled DDL (`op.create_table`,
`op.create_index`, ...), never inside a raw SQL string passed to
`op.execute`. The same asymmetry, for the same reason, is why the
migration's `op.add_column`/`op.drop_column` calls for the two new
`fact_fine_summary` columns also branch on dialect instead of always
passing `schema='fines'` -- unlike `create_table`/`create_index`, Alembic's
ADD/DROP COLUMN DDL renders the schema-qualified table name directly
rather than through the connection's translate map, so the same
literal-schema-prefix problem shows up there too on SQLite.

## Schema-change checklist

Per `docs/legacy/root-CLAUDE.md`/`./CLAUDE.local.md`: a **fines**-schema
change touches, together, in one commit -- the relevant `app/models/*.py`
file, a new Alembic migration (`alembic/versions/`),
`docs/mars_fines_projection_schema.sql`, and `tests/test_migration_parity.py`
should still pass (it builds one SQLite DB via the migration and another via
`Base.metadata.create_all()`, then diffs them -- a real check that the two
never drift apart). A **cmir**-schema change touches the model file and a
new migration; `docs/mars_fines_projection_schema.sql` is fines-only and
doesn't need updating for cmir tables.

## Tables (fines schema)

| Table | Business key | Notes |
|---|---|---|
| `dim_agent` | `agent_name` | See "Agent/prompt registry" above |
| `dim_prompt_version` | `(agent_id, prompt_version)` | See "Agent/prompt registry" above |
| `dim_retailer` | `retailer_id` | `stacking_mode` (SUM/MAX) is per-retailer, unconfirmed with either mock retailer |
| `dim_sku` | `sku_id` | |
| `dim_location` | `location_id` | |
| `dim_carrier` | `carrier_id` | `historical_reliability_score` drives the delay-model multiplier |
| `dim_fine_rule` | `rule_id` | `threshold_pct` is a fraction (0.02 = 2%), enforced by `FineRule.__post_init__` |
| `dim_fine_rule_tier` | `tier_id` | Only populated for `calc_type = TIERED` rules |
| `fact_order` | `order_id` | |
| `fact_order_confirmation` | `confirmation_id` | Historized |
| `fact_production_schedule` | `production_id` | Historized, keyed by (sku_id, location_id) |
| `fact_shipment` | `shipment_id` | Historized |
| `fact_demand_exception` | `exception_id` | |
| `fact_projected_fine` | -- (see above) | One row per order/rule/day |
| `fact_actual_fine` | `actual_fine_id` | Populated post-delivery, for calibration (Phase 2) |
| `fact_fine_summary` | -- (see below) | LLM-generated fine-summary audit trail, one row per order/day/prompt-version |
| `job_run` | -- (no status column, see above) | One row per batch trigger |
| `job_item` | -- (see "Batch job queue" above) | Work ledger; in-flight uniqueness enforced by a migration-only partial index |
