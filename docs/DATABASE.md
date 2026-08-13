# Database

Conceptual/canonical schema: `docs/mars_fines_projection_schema.sql`.
Runnable ORM: `app/models/`. Migrations: `alembic/` (repo root, run
with `alembic upgrade head`). Sample data: `data/samples/mars_fines_mock_seed_data.sql`.

## Primary keys

Every table has a surrogate `id` (UUIDv7, time-ordered) as its actual
primary key -- the standard convention, matching the sibling
`cmir_agent` project. Business identifiers (`order_id`, `retailer_id`,
`rule_id`, `sku_id`, etc.) are separate `unique=True` columns: they're
what the API, tests, and every worked example already address resources
by (`/orders/WMT-100234`, not `/orders/<uuid>`), and foreign keys
reference them directly rather than the surrogate `id` -- a FK can
target any uniquely-constrained column, not only the primary key, and
doing it this way meant adopting the `id` convention didn't require
rewriting how every repository looks up or relates rows.

`app/db/base.py::generate_uuid7()` (a Python-side default on every
model's `id` column) stays the real, only id-generation path the
application itself uses -- unconditionally, unchanged. The initial
migration (`alembic/versions/5589e602eefa_initial_schema.py`) additionally sets
`server_default=uuidv7()` (Postgres's own builtin, 18+) directly on every
table's `id` column via raw DDL, purely as a migration-level safety net
for a hypothetical future non-ORM writer that inserts without supplying
`id` -- not a change to how this app generates ids. Version-gated to
Postgres 18+ (`server_version_info`), and a no-op on SQLite (no
`uuidv7()` function there, and none of this project's tests write around
the ORM's own default anyway).

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
which is what makes `--date` backfills (via `scripts/run_projection_cli.py`
or `POST /projections/run`) reflect what was actually known on that day,
not today's current state. See `docs/FINE_ENGINE.md` changelog for
why `fact_shipment` specifically needed fixing to follow this pattern.

`fact_production_schedule` is keyed by `(sku_id, location_id)`, not
`order_id` -- a production line serves whichever orders draw on it. Two
orders that share a line will see each other's status updates; see
`tests/test_known_limitations.py` for the one place in the mock data
where that's a real, accepted wrinkle rather than a bug.

## Postgres schema separation (`fines` / `public` / `cmir`)

This service is one domain inside a physical Postgres database shared with
the sibling `cmir` project, so tables are not split across separate
databases but across separate **schemas**:

- **`fines`** -- every table this project owns (all 16, see "Tables"
  below), plus this project's own Alembic bookkeeping table
  (`fines.alembic_version`). Declared once, in `app/db/base.py::FINES_SCHEMA`,
  and `Base.metadata` is bound to it -- nothing in `app/` qualifies a table
  name by hand, and every existing bare `ForeignKey("dim_x.y")` string
  keeps working unchanged (SQLAlchemy resolves an unqualified FK string
  against the owning table's `metadata.schema`).
- **`public`** -- reserved for the shared, cross-domain tables the sibling
  `cmir` project owns (agent registry, agent runs/traces, HITL actions).
  This project does not create, migrate, or FK into anything in `public` --
  that seam is deliberately not built yet (documented separately, not
  here). Never route a `fines`-schema migration through `public`.
- **`cmir`** -- planned, not yet in use. Reserved so the sibling project can
  move its own tables into a dedicated schema later without another
  `public`-splitting migration.

On SQLite (the whole test suite, plus the `sqlite:////tmp/...` local-dev
recipe), schemas don't exist at all -- `app/db/session.py` and
`alembic/env.py` both apply SQLAlchemy's `schema_translate_map` at the
connection level to translate `fines` away, so nothing in `app/` or
`tests/` needs to know schemas are involved.

### Migration history was squashed to a single `5589e602eefa`

The `0001`-`0011` incremental chain that built up the `fines` schema
split, several renames (`dim_penalty_rule` -> `dim_fine_rule`,
`explanation` -> `narrative` -> `summary`), and audit-column backfills has
been squashed into a single `alembic/versions/5589e602eefa_initial_schema.py` --
no environment this had shipped to carried real data (demo/seed rows
only), so there was nothing an incremental history needed to preserve,
and every residual naming/constraint-shape issue that history had
accumulated (stale `dim_penalty_rule_*` constraint names, a duplicate
unique-index-vs-constraint shape, stale `fact_projection_explanation_*`
names) is gone with it -- `5589e602eefa` creates every table fresh, with today's
names, verified to produce zero drift against `alembic revision
--autogenerate` on a real Postgres 18.

A database still on the old chain (revision `0002` through `0011`) has no
forward path onto the new `0001` -- see `docs/RUNBOOK.md` §3 "Migration
history was squashed to a single `5589e602eefa`" for the drop/recreate steps.

### Cross-domain integration seam (`public.*` cmir tables)

The "documented separately, not here" pointer above, fulfilled: this
project does not create, migrate, or FK into any `public.*` table. The
sibling `cmir_agent` project's current tables (`agent_runs`, `agent_trace`,
`hitl_actions`, `pending_human_actions` -- all still `public`-qualified; no
`agent_registry` table exists anywhere yet) are not domain-agnostic today --
`agent_runs` carries `email_id`/`run_type` defaulting to `'email_ingest'`,
`workflow_threads` carries `cmir_status` -- so nothing on this side is built
against that shape or assumes it's final.

If/when those tables generalize and a `fines`-side row (most likely
`fact_fine_summary`, given it already carries an LLM-generation
audit trail) should reference an `agent_run_id`, that's a future, explicit
follow-up -- no speculative nullable column is being added now, since
guessing the wrong type would be worse than waiting.

One mismatch to flag for joint resolution now, rather than let either side
silently absorb it later: `cmir_agent.agent_runs.id` is a plain integer
`SERIAL` primary key, while every table in this project uses a UUIDv7
surrogate `id` (see "Primary keys" above). If a `fines` table ever needs to
FK into `public.agent_runs`, that type mismatch needs a decision from
whoever owns the shared schema design -- not a unilateral adaptation by
either side.

## Agent/prompt registry

`dim_agent` and `dim_prompt_version` (part of the initial migration) give
`fact_fine_summary.prompt_version` a real relationship to the agent that
generated it and the module the prompt text actually lives in, in place
of a bare unchecked string. Deliberately generic (agent_name +
prompt_version, not fine-summary-specific) and deliberately kept in the
`fines` schema for now -- see "Postgres schema separation" above for why
a live, FK'd table isn't routed through `public` unilaterally; relocating
this pair there later, if/when the sibling `cmir` project's own agent
registry generalizes, is a future, explicit, coordinated migration.
`dim_agent.source` (`"fines"` or `"cmir"`) records which project actually
owns/runs a given agent, so that eventual move doesn't lose which side
each row belongs to.

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
on it. Same "code is the source of truth for what exists, migrations only
shape the schema" split already used for `dim_retailer`/`dim_sku` (seeded
via `SeedingService`). Bumping the fine-summary prompt to a new version (a
new `app/agents/prompts/fine_summary/vN.py`) self-registers on the next
call; nothing has to be remembered or run by hand.

## Schema-change checklist

Per `docs/legacy/root-CLAUDE.md`/`./CLAUDE.local.md`: a schema change touches, together, in one commit --
the relevant `app/models/*.py` file, a new Alembic migration
(`alembic/versions/`), `docs/mars_fines_projection_schema.sql`, and
`tests/test_migration_parity.py` should still pass (it builds one SQLite
DB via the migration and another via `Base.metadata.create_all()`, then
diffs them -- a real check that the two never drift apart).

## Tables

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
