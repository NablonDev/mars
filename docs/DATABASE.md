# Database

Canonical schema reference: `docs/mars_penalties_erp_schema.sql` (Postgres-specific
DDL, hand-maintained, kept 1:1 with `app/models/`). Runnable ORM: `app/models/`.
Migrations: `alembic/` (repo root, run with `alembic upgrade head`). Sample data:
`data/samples/mars_fines_mock_seed_data.sql` (pending a rename to match the
penalty domain rename -- out of scope for this phase).

## Postgres schema separation (`common` / `process` / `cmir` / `penalties` / `langgraph`)

This one FastAPI app hosts two domains against one physical Postgres
database, split across five **schemas**. `public` holds no domain tables
at all.

- **`common`** -- shared master data (`retailer`, `sku`, `material`/
  `material_master`, `plant`/`storage_location`/`warehouse`,
  `retailer_location`, `carrier`) and fulfillment facts (`purchase_order`/
  `purchase_order_line`, `order_confirmation`/`*_line`, `delivery`/
  `*_line`/`shipment`, `production_order`/`production_schedule`,
  `demand_exception`), used by both the `cmir`/`po_validation` and
  `penalties` domains. Declared in `app/db/base.py::COMMON_SCHEMA`.
- **`process`** -- the shared job/agent/workflow backbone (`job_run`,
  `job_item`, `workflow_thread`, `workflow_thread_subject`, `agent`,
  `agent_run`, `agent_trace`, `human_action`, `processing_error`),
  consolidating what were two parallel stacks before this restructure: the
  old `fines` schema's `job_run`/`job_item`, and the `cmir` schema's
  `agent_runs`/`agent_traces`/`workflow_threads`/`hitl_actions`/
  `pending_human_actions`. Declared in `app/db/base.py::PROCESS_SCHEMA`.
  Alembic's own version table lives here too (see "Migration history"
  below).
- **`cmir`** -- CMIR/PO-validation-only tables (`email_event`,
  `email_action_log`, `cmir_record`, `cmir_job_run_context`,
  `cmir_job_item_context`). Declared in `app/db/base.py::CMIR_SCHEMA`.
- **`penalties`** -- penalties-only tables (full `fine`/`fines` ->
  `penalty`/`penalties` domain rename; see "Tables (penalties schema)"
  below). Declared in `app/db/base.py::PENALTIES_SCHEMA`.
- **`langgraph`** -- created empty by its own migration
  (`a5b39c6e2181_langgraph_schema.py`). LangGraph's `PostgresSaver`
  creates and owns its checkpoint tables (`checkpoints`,
  `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) here at
  runtime, never via Alembic, and never queried/written directly by
  application code. No ORM model is bound to this schema.

Every FK across all four ORM-owned schemas is a `uuid -> <schema>.<table>.id`
surrogate-key reference, schema-qualified in the FK string
(`ForeignKey(f"{COMMON_SCHEMA}.purchase_order.id")`) -- nothing relies on the
connection's default `search_path`. This is the single largest mechanical
change from the pre-restructure schema, where most FKs pointed at a
business-key column (e.g. `Order.retailer_id -> fines.retailer.retailer_id`,
a `String(50)`).

On SQLite (the whole test suite, plus the `sqlite:////tmp/...` local-dev
recipe), schemas don't exist at all -- `app/db/session.py` and
`alembic/env.py` both apply SQLAlchemy's `schema_translate_map` at the
connection level to translate `common`, `process`, `cmir`, and `penalties`
away (never `langgraph` -- no ORM model is bound to it, so there is
nothing to translate).

**A structural consequence of that flattening, worth knowing before adding
a new domain-owned extension table:** two tables with the same bare name
in two different schemas (e.g. a naive `job_run_context` in both `cmir`
and `penalties`) collide once SQLite flattens both schemas to the same
unqualified namespace, and `Base.metadata.create_all()` fails outright.
This is why `cmir`'s and `penalties`' `job_run_context`/`job_item_context`
extension tables are actually named `cmir_job_run_context`/
`cmir_job_item_context` and `penalty_job_run_context`/
`penalty_job_item_context` -- prefixed to match their already-prefixed
Python class names (`CmirJobRunContext`/`PenaltyJobRunContext`, chosen to
avoid a *class*-name collision in one `Base` registry), not left bare as
an earlier plan draft specified.

### Migration history

Five revisions, one per schema, in FK-dependency order:

| Revision | File | What it adds |
|---|---|---|
| `0824321a02a4` | `alembic/versions/0824321a02a4_common_schema.py`, `down_revision=None` | Every `common`-schema table |
| `ff53dabe6e4c` | `alembic/versions/ff53dabe6e4c_process_schema.py` | Every `process`-schema table except `workflow_thread_subject` (see below) |
| `374aa902b053` | `alembic/versions/374aa902b053_cmir_schema.py` | Every `cmir`-schema table, **plus `process.workflow_thread_subject`** |
| `4b41f6bcb2f3` | `alembic/versions/4b41f6bcb2f3_penalties_schema.py` | Every `penalties`-schema table |
| `a5b39c6e2181` | `alembic/versions/a5b39c6e2181_langgraph_schema.py` | `CREATE SCHEMA langgraph` only -- no tables, Postgres-only, no-op on SQLite |

**Why `workflow_thread_subject` is created by the `cmir` revision, not the
`process` one its Python class lives in:** a genuine cross-revision FK
cycle. `process.workflow_thread_subject.email_event_id` FKs into
`cmir.email_event.id` (so `process` needs `cmir` to exist first), while
`cmir.cmir_job_item_context.job_item_id` FKs into `process.job_item.id`
(so `cmir` needs `process` to exist first). Postgres requires a FK's
target table to exist at `CREATE TABLE` time (SQLite doesn't enforce
this, which is why this class of bug only surfaces against a real
Postgres database -- see "Manual, against live Postgres" verification
below). The fix is birthplace, not reordering: the table is created by
whichever revision runs second, in the schema that consumes the earlier
dependency.

This is a full re-squash of the previous 3-revision chain
(`e803d9470f31` -> `43d8ced96170` -> `e04c67e98dda`, itself the result of
an earlier squash -- see git history for the pre-squash chain this
replaced). Nothing on the replaced chain had shipped to an environment
carrying real data (demo/seed rows only), so there was nothing an
incremental history needed to preserve.

**Local dev DB:** this squash is not reversible against pre-existing data
(table/column renames throughout, not just additive changes). Drop and
recreate the local dev database against this chain.
`scripts/ops/repair_pre_squash_db.py` does not cover this jump -- it only
migrates a database stranded on the pre-*previous*-squash chain forward
to the 3-revision chain this squash itself replaces.

## Known gaps / not modeled yet

`inventory_position` and `demand_forecast` -- mentioned in an earlier
draft of the redesigned schema (`docs/redesigned-schema.md`) -- have no
ORM class, no migration, and no locked-in column design. They are
deliberately **not** part of this schema; do not add them speculatively.
If a future feature genuinely needs either, that's a new modeling
decision, not a mechanical carry-forward from the draft doc.

## CMIR / PO Validation tables

All of the following live in the `cmir` schema (`app/models/cmir/`).

### `email_event` (`EmailEvent`)
One row per inbound email, doubling as the Service Bus queue's work item.
Renamed from `email_events` (singular, matching the rest of this schema's
naming convention).

### `email_action_log` (`EmailActionLog`)
Append-only audit log of email-level workflow events. Renamed from
`email_action_logs`.

### `cmir_record` (`CmirRecord`)
SCD2 history of approved CMIR mappings -- **not** an append-only log.
Exactly one `is_current = TRUE` row per `(customer_identity_key,
target_customer_material_ref_key)`, enforced by a partial unique index
(`uq_cmir_record_current_identity`, migration-only raw DDL). Renamed from
`cmir_records`; its `po_line_id` FK is renamed `purchase_order_line_id`
and now points at `common.purchase_order_line.id` (a real table, not the
CMIR-schema stub `po_lines` used to be).

### `cmir_job_run_context` (`CmirJobRunContext`) / `cmir_job_item_context` (`CmirJobItemContext`)
Extension tables for `process.job_run`/`process.job_item`, carrying
CMIR-specific batch-ingest metadata and which email/PO line a work item is
about, respectively. `cmir_job_item_context` carries a
`CHECK (num_nonnulls(email_event_id, purchase_order_line_id) = 1)`
(migration-only raw DDL -- `num_nonnulls` is a PostgreSQL-only builtin).
See "Why bare `job_run_context`/`job_item_context` doesn't work" above for
why these table names are prefixed.

### LangGraph checkpoint tables
Created and owned entirely by `PostgresSaver.setup()` -- never created via
Alembic and never queried/written directly by application code. Both
agents' graphs share one `PostgresSaver` instance; checkpoint `thread_id`s
are namespaced so the two graphs' checkpoints never collide. Now created
in the dedicated `langgraph` schema, not `public`.

## `process` schema -- shared job/agent/workflow backbone

### `job_run` (`JobRun`)
One row per batch trigger (`SCHEDULED_DAILY`/`MANUAL_BATCH`/`ON_DEMAND`,
via `job_type`+`trigger_type`). Deliberately has **no status column** --
concurrent workers update `job_item` rows continuously while a run is in
flight, and a status column here would mean every one of them also has to
lock this single shared row, a hot-lock bottleneck for no real benefit. A
run's status is derived at read time by grouping `job_item` rows by
`job_run_id`/`status`. This is a real, deliberate divergence from an
earlier draft of the redesigned schema (`docs/redesigned-schema.md`),
which listed a `status` column on this table -- that draft was written
without this codebase's context. Do not add one back without re-deriving
why this was avoided.

### `job_item` (`JobItem`)
Generic work ledger shared by both domains -- one row per unit of work
(`item_type`: `EMAIL_INGEST`/`PO_VALIDATION`/`ORDER_RUN`/
`PROJECTION_SUMMARY_REGEN`/`MITIGATION_SUMMARY_REGEN`). `status` moves
`PENDING -> RUNNING -> SUCCEEDED`, or `PENDING -> RUNNING -> PENDING`
(retry) `-> ... -> DEAD`. There is deliberately **no resting `FAILED`
state**. `id` is a UUIDv7, doubling as a FIFO tiebreaker. Domain-specific
columns the pre-restructure `job_item` used to carry (`order_id`,
`projection_date`, `stacking_mode_override`, `force_regenerate_summary`)
now live on each domain's own `job_item_context` extension table
(`cmir.cmir_job_item_context`, `penalties.penalty_job_item_context`).
`penalties.penalty_job_item_context.task_type` is a real exception: it
still duplicates this table's own `item_type` column, with nothing keeping
the two in sync. `item_type` (here, on `process.job_item`) is the source
of truth -- it's what `uq_job_item_inflight` (below) actually indexes; any
new code should read it, not `task_type`.

**`dedupe_key` (`varchar(200)`, nullable) restores in-flight dedupe** on
top of a generic column, via the partial unique index
`uq_job_item_inflight` on `(item_type, dedupe_key) WHERE status IN
('PENDING', 'RUNNING') AND dedupe_key IS NOT NULL` (migration-only raw
DDL -- see "Migration-only raw DDL constructs" below). This replaces the
pre-restructure `uq_job_item_inflight`'s business key (`order_id`,
`projection_date`, `task_type`), which no longer lives on this
generic, domain-shared table. `dedupe_key` itself is queue
infrastructure, not a domain column -- the domain layer computes and
passes the string at enqueue time (a repository-layer concern, Phase 2/3,
e.g. `f"{purchase_order_id}:{projection_date}"` for penalties, the email
event id for CMIR); rows that don't need dedup leave it `NULL`, which the
index's `dedupe_key IS NOT NULL` clause excludes entirely.

**Correction:** an earlier version of this document claimed
`JobQueueRepository` already had an application-level fallback that made
the DB constraint above non-essential. That was false: the repository's
own `enqueue_many()` calls `pg_insert(...).on_conflict_do_nothing(
index_elements=..., index_where=...)`, which requires a matching index to
exist on Postgres -- it raises at runtime otherwise. The partial index is
the primary defense on Postgres, not a backstop; `JobQueueRepository`'s
SQLite fallback (a Python-side dict scan) exists only because SQLite has
no equivalent conflict-resolution clause to hang the index off of, not
because the index itself is optional.

### `workflow_thread` (`WorkflowThread`) / `workflow_thread_subject` (`WorkflowThreadSubject`)
The reviewer-facing HITL thread, only created once a job item's first
human interrupt fires. `workflow_thread_subject` replaces the old
polymorphic `subject_type`/`subject_id` pair with two nullable typed FKs
(`email_event_id`, `purchase_order_line_id`) plus a
`CHECK (num_nonnulls(email_event_id, purchase_order_line_id) = 1)`
(migration-only raw DDL, Postgres-only). See "Migration history" above
for why this table is created by the `cmir` revision.

### `agent` (`Agent`)
Merges what were two tables (`agent` + `prompt_version`) into one: one row
per `(agent_code, prompt_version)` pair, with `system_prompt` (the actual
prompt text) stored directly rather than pointed at by a `module_path`
string. `domain` (`"cmir"` | `"penalties"`, `NOT NULL`, `CHECK (domain IN
('cmir', 'penalties'))` -- `ck_agent_domain`) records which domain
owns/runs a given agent. Downstream summary FKs (`penalty_summary.agent_id`)
now point at `agent.id` directly -- a single-column FK, replacing the old
composite `(agent_id, prompt_version) -> prompt_version(agent_id,
prompt_version)`.

Rows are seeded by `scripts/seed/seed_agents.py` (standalone runnable:
`python -m scripts.seed.seed_agents`), idempotent on `(agent_code,
prompt_version)`. `process.agent.system_prompt` is a new persistent store
of LLM system-level instructions -- a future prompt-injection surface once
a later phase wires the runtime load; no user-writable path may ever reach
this column and no API endpoint may expose a write to it.

**`uq_agent_one_active_per_code`** -- a partial unique index on
`(agent_code) WHERE is_active` (migration-only raw DDL -- see
"Migration-only raw DDL constructs" below) -- guarantees at most one
active prompt version per agent code, matching the seed data's shape (each
of the four `agent_code`s has exactly one `is_active = TRUE` row).

### `agent_run` (`AgentRun`) / `agent_trace` (`AgentTrace`)
One row per independent agent execution, and one row per LangGraph node
execution within it, respectively. Both now shared by the `cmir` and
`penalties` domains via `agent_id`/`run_type`, rather than being CMIR-only.

### `human_action` (`HumanAction`)
Merges what were two tables (`pending_human_actions` + `hitl_actions`)
into one -- the open/completed interrupt and the audit trail of its answer
are now one row's lifecycle, not two separately-written tables.

### `processing_error` (`ProcessingError`)
Generalizes the old CMIR-only `PoLineErrorORM`/`po_line_errors` -- system/
lookup failures, distinct from `human_action` (human decisions). FKs to
`process.job_item.id`/`process.agent_run.id`; the domain-specific
`po_line_id` link that table used to carry now lives on
`cmir.cmir_job_item_context` instead.

## Primary keys

Every table in every ORM-owned schema has a surrogate `id` (UUIDv7,
time-ordered) as its actual primary key, generated client-side by
`app/db/base.py::generate_uuid7()` -- no `id` column carries a
database-side default. Business identifiers (`purchase_order_number`,
`retailer_code`, `rule_code`, `sku_code`, etc.) are separate `unique=True`
columns, and foreign keys reference the surrogate `id` exclusively now --
unlike the pre-restructure schema, where most FKs pointed at a business
key. `job_run_context`/`job_item_context`-shaped extension tables
(`cmir_job_run_context`, `penalty_job_item_context`, ...) and
`workflow_thread_subject` are the one deliberate exception: their PK *is*
the FK into the table they extend (`job_run_id`, `job_item_id`,
`workflow_thread_id`), a 1:1 subtype/extension-table pattern, not a
missing surrogate key.

## Historization

`order_confirmation`, `production_schedule`, and `shipment` (all in
`common`) are append-only: every write is a new row, never an update to an
existing one. `production_schedule` is keyed by `(material_id, plant_id)`,
not a single PO -- a production line serves whichever POs draw on it. Two
POs that share a line will see each other's status updates; see
`tests/unit/services/test_known_limitations.py` for the one place in the
mock data where that's a real, accepted wrinkle rather than a bug (the key
moved from the pre-restructure `(sku_id, location_id)` to
`(material_id, plant_id)` -- re-verify this collision still actually
collides under the new keys before relying on that test unchanged).

## Tables (penalties schema)

Full `fine`/`fines` -> `penalty`/`penalties` domain rename.

| Table | Business key | Notes |
|---|---|---|
| `penalty_rule` (`PenaltyRule`) | `rule_code` | Renamed from `fine_rule`/`rule_id` |
| `penalty_rule_tier` (`PenaltyRuleTier`) | `(rule_id, tier_code)` | Renamed from `fine_rule_tier`; only populated for `calc_type = TIERED` rules |
| `penalty_job_run_context` (`PenaltyJobRunContext`) / `penalty_job_item_context` (`PenaltyJobItemContext`) | -- | Extension tables for `process.job_run`/`process.job_item`, carrying `purchase_order_id`, `projection_date`, `task_type`, `stacking_mode_override`, `force_regenerate_summary` |
| `mitigation_input` (`MitigationInput`) | `purchase_order_id` (unique) | Mutable current-best-guess cause/cost assumptions; no soft delete/history, deliberately unlike every append-only table in `common` |
| `mitigation_option` (`MitigationOption`) | `(purchase_order_id, projection_date, action)` | Renamed from `MitigationResult` -- unified on the same name the pure-engine dataclass already used, since both now live in clearly separate modules |
| `penalty_summary` (`PenaltySummary`) | `(purchase_order_id, summary_type, as_of_date)` | Merges what were two tables (`projection_summary`, `mitigation_summary`) into one, with a `summary_type` (`PROJECTION`\|`MITIGATION`) discriminator |
| `penalty_projection` (`PenaltyProjection`) | `(purchase_order_id, rule_id, projection_date)` | Renamed from `projected_fine` |
| `actual_penalty` (`ActualPenalty`) | `actual_penalty_number` | Renamed from `actual_fine` |
| `po_delivery_change_request` (`PoDeliveryChangeRequest`) | `request_id` | Renamed from `purchase_order_delivery_change_request` (itself renamed from the original `po_delivery_change_request`, back when the FK was also renamed `order_id` -> `purchase_order_id` to point at the surrogate `common.purchase_order.id`) |

## Why no `dim_`/`fact_` prefix

Every table across all four ORM-owned schemas is singular, `snake_case`,
with no `dim_`/`fact_` prefix (`retailer`, `purchase_order`,
`penalty_projection`, ...) -- this is an operational REST backend serving
live reads and writes behind a FastAPI app, not a data warehouse queried by
a BI tool. There's no star schema, no slowly-changing dimension tooling
(`cmir_record`'s SCD2 versioning is a domain-specific exception, not a
warehouse pattern), no separate ETL layer that benefits from a table name
advertising its role in a load pipeline. This convention predates the
current restructure -- see git history for the original squash that
established it -- and is carried forward unchanged.

## Migration-only raw DDL constructs

Six constructs across the five revisions cannot be expressed as an ORM
model declaration, and exist only as raw DDL inside their migration:

1. **`workflow_thread_subject`'s `CHECK (num_nonnulls(email_event_id,
   purchase_order_line_id) = 1)`** (`374aa902b053_cmir_schema.py`) --
   PostgreSQL-only builtin, skipped on SQLite.
2. **`cmir_job_item_context`'s identical CHECK** (same migration, same
   reason).
3. **`cmir_record`'s partial unique index**
   (`uq_cmir_record_current_identity`, same migration) -- SQLAlchemy's
   `postgresql_where=` on an `Index` is silently dropped on SQLite, which
   would otherwise create a full unique index there and wrongly reject a
   legitimate second historical (non-current) row for the same identity
   pair.
4. **`job_item`'s partial unique index** (`uq_job_item_inflight`,
   `ff53dabe6e4c_process_schema.py`) -- restores the pre-restructure
   schema's in-flight dedupe constraint on top of the generic `dedupe_key`
   column; same "`postgresql_where=` dropped on SQLite" reason as #3. See
   `job_item`'s own section above for the full story, including why an
   earlier version of this squash dropped it and why that was wrong.
5. **`agent`'s partial unique index** (`uq_agent_one_active_per_code`,
   same migration) -- at most one active prompt version per agent code.
6. **`langgraph`'s `CREATE SCHEMA`** (`a5b39c6e2181_langgraph_schema.py`)
   -- Postgres-only, no SQLite equivalent, no-op there.

All six are dialect-branched (schema-qualified on Postgres, unqualified
or skipped on SQLite) so `tests/unit/db/test_migration_parity.py`'s
SQLite `alembic upgrade head` run still exercises the surrounding
migration cleanly. That test itself only diffs table/column *names* --
never indexes, constraints, types, or nullability -- so it cannot catch a
divergence in any of the six constructs above; they're verified only
against a real Postgres database.

**`alembic check` / `alembic revision --autogenerate` will always flag
constructs #1-5 as phantom drops.** Autogenerate compares reflected
Postgres DDL against `Base.metadata`; none of a `postgresql_where=`
partial index or a raw `ALTER TABLE ... ADD CONSTRAINT CHECK
(num_nonnulls(...))` is represented in the ORM models, so every future
`alembic check`/autogenerate run against a freshly-migrated database will
report all five as present-in-DB-but-absent-from-metadata and try to emit
a matching `op.drop_index(...)`/`op.drop_constraint(...)` in `upgrade()`.
This is expected, not a regression -- hand-strip any such line before
committing a new migration, the same way `374aa902b053` and
`ff53dabe6e4c` themselves had to be hand-fixed after their own
autogenerate pass (`grep -n "drop_index" alembic/versions/*.py` should
only ever match inside a `downgrade()`, never an `upgrade()` -- see the
approved Phase 1 plan's "recurring Alembic autogenerate defect" note for
the full mechanics). Construct #6 (`CREATE SCHEMA langgraph`) is not
affected -- it creates no table/index/constraint for autogenerate to
compare against metadata at all.

## Schema-change checklist

Per `CLAUDE.local.md`'s "Engineering Rules": a schema change touches,
together, in one commit -- the relevant `app/models/*.py` file, a new
Alembic migration (`alembic/versions/`), `docs/mars_penalties_erp_schema.sql`,
this document (if the change is structural, not just a column tweak), and
`tests/unit/db/test_migration_parity.py` should still pass (it builds one
SQLite DB via the migration and another via `Base.metadata.create_all()`,
then diffs them -- a real check that the two never drift apart, though
only at the table/column-name level; verify types/constraints/indexes
against a real Postgres database separately).
