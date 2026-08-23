# Database

Canonical schema reference (fines domain only): this document.
Runnable ORM for both domains: `app/models/`. Migrations: `alembic/` (repo root, run
with `alembic upgrade head`). Sample data: `data/samples/mars_fines_mock_seed_data.sql`.

## Postgres schema separation (`cmir` / `fines` / `public`)

This one FastAPI app hosts two domains against one physical Postgres
database, split across separate **schemas**:

- **`cmir`** -- every table the CMIR/PO-validation domain owns, declared in
  `app/db/base.py::CMIR_SCHEMA`. This currently includes CMIR's own
  domain tables (`cmir_records`, `email_events`, `email_action_logs`,
  `po_lines`, `material_master`, `po_line_errors`, `workflow_threads`)
  *and* its per-run observability/HITL tables (`agent_runs`,
  `agent_traces`, `hitl_actions`, `pending_human_actions`) -- see "CMIR /
  PO Validation tables" below for the per-table reference.
- **`fines`** -- every table the Projected Fines domain owns (all 21, see
  "Tables" below). Declared in `app/db/base.py::FINES_SCHEMA`.
- **`public`** -- holds no domain tables of either project's yet (a future
  split that moves the genuinely cross-domain agent-observability tables
  -- `agent_runs`, `agent_traces`, `hitl_actions` -- into `public` so a
  third, unrelated agent project could consume them without depending on
  `cmir` internals is a real idea that came up while merging the two
  domains together, but was explicitly deferred, not implemented in this
  pass; if/when it happens, `workflow_threads` and `pending_human_actions`
  stay in `cmir` since they FK into cmir-private tables like
  `email_events`/`po_lines`, so revisit this split table-by-table rather
  than moving the whole set at once). It does already hold Alembic's own
  bookkeeping table, `public.alembic_version` -- one linear migration
  history covers both domain schemas, so that table belongs in the schema
  neither domain owns, not tucked inside `fines` or `cmir` as if it were
  one domain's private state.

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
| `e803d9470f31` | `alembic/versions/e803d9470f31_initial_fines_schema.py`, `down_revision=None` | Every `fines`-schema table, with every `dim_`/`fact_` table already renamed to its final name (see "Why no dim_/fact_ prefix" below) |
| `43d8ced96170` | `alembic/versions/43d8ced96170_initial_cmir_schema.py`, `down_revision=e803d9470f31` | Every `cmir`-schema table |

This is a full re-squash of what was, before it, an 11-revision chain
spanning both schemas (`5589e602eefa` through `176a1ab0e4f3`) -- itself
already the result of an earlier squash of the original fines `0001`-`0011`
chain and the CMIR branch's own `0001`-`0005` chain. Nothing on that
11-revision chain had shipped to an environment carrying real data
(demo/seed rows only), so there was nothing an incremental history needed
to preserve, and every residual naming/type issue it had accumulated
(the `dim_`/`fact_` prefixes themselves, stale `dim_penalty_rule_*`
constraint names from an even earlier pass, `agent_trace` ->
`agent_traces`, `agent_runs.id` `SERIAL` -> UUIDv7) is gone with it. A
database still on the old 11-revision chain -- or the chain before that --
has no forward path onto the new one -- see `docs/RUNBOOK.md`'s "Database:
migrate" section for the drop/recreate steps.

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

### `email_action_logs` (`EmailActionLogORM`)
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
| `agent_runs`, `workflow_threads`, `pending_human_actions`, `hitl_actions`, `agent_traces`, `cmir_records` | `email_events`, `email_action_logs` | `po_lines`, `material_master`, `po_line_errors` |

## Primary keys

Every fines-domain table, plus `agent_runs` and everything that FKs into it
(`agent_traces`, `hitl_actions`, `pending_human_actions`, `workflow_threads`,
`po_line_errors`), has a surrogate `id` (UUIDv7, time-ordered) as its actual
primary key. Business identifiers (`order_id`, `retailer_id`, `rule_id`,
`sku_id`, etc.) are separate `unique=True` columns: they're what the API,
tests, and every worked example already address resources by
(`/orders/WMT-100234`, not `/orders/<uuid>`), and foreign keys reference
them directly rather than the surrogate `id` where a business key exists.
A handful of CMIR/PO-validation tables (`cmir_records`, `email_action_logs`,
`hitl_actions`, `pending_human_actions`, `agent_traces` themselves) still
use a plain integer PK -- untouched by this convention, since they have no
business key and nothing FKs into them from outside their own domain.

`app/db/base.py::generate_uuid7()` (a Python-side default on every
UUID-surrogate `id` column) is the id-generation path the application
itself uses. The initial fines migration
(`alembic/versions/e803d9470f31_initial_fines_schema.py`) additionally sets
`server_default=uuidv7()` (Postgres's own builtin, 18+) directly on every
`fines`-schema table's `id` column via raw DDL, purely as a migration-level
safety net for a hypothetical future non-ORM writer that inserts without
supplying `id`. Version-gated to Postgres 18+, and a no-op on SQLite.

One exception: `projected_fine` has no single natural key -- its
business identity is the triple `(order_id, rule_id, projection_date)`,
enforced as a `UniqueConstraint`, which is what
`ProjectionRepository.save_result` upserts against.

`projection_summary.prompt_version` is no longer a bare string: alongside
a new `agent_id` column (no default -- always supplied by
`FineProjectionSummaryService`), it's a composite FK into
`prompt_version(agent_id, prompt_version)` -- see "Agent/prompt
registry" below.

Similarly, `projection_summary`'s business identity is the
triple `(order_id, as_of_date, prompt_version)` -- a prompt-version bump
is a deliberate content change, so a summary generated under an old
prompt must never be served in place of one generated under a new one.
Generation is a background job (`FineProjectionSummaryService.get_or_schedule`/
`run_generation`, see app/api/v1/fine_projection/summaries.py): a row moves through
`status` PENDING -> READY or PENDING -> FAILED, and a fresh request
against an already-existing key (a cache miss re-request, or
`force_regenerate`) overwrites that row in place
(`FineProjectionSummaryRepository.create_pending`) rather than inserting a
second one -- this table holds the *latest* summary job per key, not a
full history of every attempt.

## Historization

`order_confirmation`, `production_schedule`, and
`shipment` are all append-only: every write is a new row, never an
update to an existing one. `OrderRepository.build_snapshot` always
selects the latest row "as of" the requested projection date from each,
which is what makes `--date` backfills (via `scripts/ops/run_projection_cli.py`
or `POST /projections/run`) reflect what was actually known on that day,
not today's current state. `shipment` specifically needed fixing to
follow this pattern.

`production_schedule` is keyed by `(sku_id, location_id)`, not
`order_id` -- a production line serves whichever orders draw on it. Two
orders that share a line will see each other's status updates; see
`tests/test_known_limitations.py` for the one place in the mock data
where that's a real, accepted wrinkle rather than a bug.

## Agent/prompt registry

`agent` and `prompt_version` (part of the fines migration) give
`projection_summary.prompt_version` a real relationship to the agent that
generated it and the module the prompt text actually lives in, in place
of a bare unchecked string. Deliberately generic (agent_name +
prompt_version, not fine-projection-summary-specific) and deliberately kept in the
`fines` schema, separate from the CMIR domain's own `agent_runs` (a
different concept -- one execution, not a named/versioned agent
definition). `agent.source` (`"fine_projection"`, `"fine_mitigation"`,
`"cmir"`, or `"po_validation"`, nullable -- no domain default) records
which domain actually owns/runs a given agent, in case these two
registries are ever unified later.

`prompt_version.agent_id`/`projection_summary.agent_id` reference
`agent.id` (the surrogate UUID), not `agent_name` -- unlike most other
FKs in this codebase (see "Primary keys" above), a deliberate choice for
this specific pair: an agent's identity here is structural/never-renamed,
not a business key callers address resources by the way `order_id`/
`retailer_id` are.

Rows are registered idempotently by `FineProjectionSummaryService` itself
(`PromptRegistryRepository.ensure_registered`), called right before the
service writes a `projection_summary` row under that key -- not at app
startup, and not seeded via a migration or a script. This keeps
registration tied to the operation that actually needs it (no separate
boot-time side effect unrelated to any specific request) while still
guaranteeing the FK is always satisfiable before the write that depends
on it. Bumping the fine-projection-summary prompt to a new version (a new
`app/agents/fine_projection/prompts/vN.py`) self-registers on the next call;
nothing has to be remembered or run by hand.

## Batch job queue (`job_run` / `job_item`)

Two tables backing the batch dispatch/claim pipeline for fine projections
and summary regeneration, added alongside two new nullable columns on
`projection_summary` (`content_fingerprint`, `source_as_of_date` --
client-facing audit-trail fields supporting idempotent regeneration and
backfill provenance).

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

`task_type` gained a third value, `MITIGATION_SUMMARY_REGEN`, alongside
`mitigation_summary` (see "Tables (fines schema)" below):
mitigation-options computation (`mitigation_option`) is always
synchronous/inline via the API, never a `job_item` -- only its LLM-powered
summary goes through this queue, the same relationship `PROJECTION_SUMMARY_REGEN` has
to `projected_fine`. The `job_item.task_type` `CHECK` constraint is
extended accordingly (Postgres-only migration; SQLite has no `ALTER TABLE
... DROP/ADD CONSTRAINT` for a table-level `CHECK`, and
`tests/test_migration_parity.py` only diffs table/column names, never
constraint bodies, so this follows the same dialect-gating precedent as
the `121840bc4fdf` constraint rename).

### The partial unique index is migration-only, not on the ORM model

`job_item` needs `UNIQUE (order_id, projection_date, task_type) WHERE
status IN ('PENDING', 'RUNNING')` (named `uq_job_item_inflight`) to stop
duplicate in-flight work for the same order/date/task while still
allowing many terminal rows across days and retries. This is declared
**only** as raw DDL in the migration
(`alembic/versions/e803d9470f31_initial_fines_schema.py`),
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
`projection_summary` columns also branch on dialect instead of always
passing `schema='fines'` -- unlike `create_table`/`create_index`, Alembic's
ADD/DROP COLUMN DDL renders the schema-qualified table name directly
rather than through the connection's translate map, so the same
literal-schema-prefix problem shows up there too on SQLite.

## Schema-change checklist

Per `docs/legacy/root-CLAUDE.md`/`./CLAUDE.local.md`: a **fines**-schema
change touches, together, in one commit -- the relevant `app/models/*.py`
file, a new Alembic migration (`alembic/versions/`), this document, and
`tests/test_migration_parity.py` should still pass (it builds one SQLite
DB via the migration and another via `Base.metadata.create_all()`, then
diffs them -- a real check that the two never drift apart). A **cmir**-schema
change touches the model file and a new migration; this document is
fines-only and doesn't need updating for cmir tables.

## Tables (fines schema)

| Table | Business key | Notes |
|---|---|---|
| `agent` | `agent_name` | See "Agent/prompt registry" above |
| `prompt_version` | `(agent_id, prompt_version)` | See "Agent/prompt registry" above |
| `retailer` | `retailer_id` | `stacking_mode` (SUM/MAX) is per-retailer, unconfirmed with either mock retailer |
| `sku` | `sku_id` | |
| `location` | `location_id` | |
| `carrier` | `carrier_id` | `historical_reliability_score` drives the delay-model multiplier |
| `fine_rule` | `rule_id` | `threshold_pct` is a fraction (0.02 = 2%), enforced by `FineRule.__post_init__` |
| `fine_rule_tier` | `tier_id` | Only populated for `calc_type = TIERED` rules |
| `sales_order` | `order_id` | |
| `order_confirmation` | `confirmation_id` | Historized |
| `production_schedule` | `production_id` | Historized, keyed by (sku_id, location_id) |
| `shipment` | `shipment_id` | Historized |
| `demand_exception` | `exception_id` | |
| `projected_fine` | -- (see above) | One row per order/rule/day |
| `actual_fine` | `actual_fine_id` | Populated post-delivery, for calibration (Phase 2) |
| `projection_summary` | -- (see below) | LLM-generated fine-projection-summary audit trail, one row per order/day/prompt-version |
| `job_run` | -- (no status column, see above) | One row per batch trigger |
| `job_item` | -- (see "Batch job queue" above) | Work ledger; in-flight uniqueness enforced by a migration-only partial index |
| `mitigation_input` | `order_id` (unique) | Mutable current-best-guess cause/cost assumptions feeding mitigation ranking; no soft delete/history, deliberately unlike every other append-only table in this schema |
| `mitigation_option` | `(order_id, projection_date, action)` | ORM class is `MitigationResult`, not `MitigationOption` -- deliberately kept apart from the pure-engine `MitigationOption` dataclass in `app/services/fine_mitigation/models.py`. Persisted, ranked output of `evaluate_mitigation_options`; computed synchronously, never via the job queue |
| `mitigation_summary` | -- (see above) | LLM-generated fine-mitigation-summary audit trail; structural mirror of `projection_summary` |

## Why no `dim_`/`fact_` prefix

Every `fines`-schema table was originally built with a Kimball-style
`dim_`/`fact_` prefix (`dim_retailer`, `fact_order`, ...), carried over
from data-warehouse modeling habits without checking whether it actually
fit this system. It didn't: this is an operational REST backend serving
live reads and writes behind a FastAPI app, not a data warehouse queried
by a BI tool -- there's no star schema, no slowly-changing dimension
tooling, no separate ETL layer that benefits from a table name
advertising its role in a load pipeline. Every table name in this schema
is singular, `snake_case`, with no `dim_`/`fact_` prefix (`retailer`,
`sales_order`, `projected_fine`, ...) -- see the `postgres-conventions`
skill for the naming rule this codifies. `fact_order` became
`sales_order` rather than plain `order` specifically to avoid colliding
with the SQL reserved word `ORDER`; every other rename is a straight
prefix drop.

No rationale for the `dim_`/`fact_` convention was ever written down
anywhere in this repo before this squash -- this section exists so the
next person asking "why did we have these prefixes, and why did we drop
them" doesn't have to reconstruct the answer from git history.
