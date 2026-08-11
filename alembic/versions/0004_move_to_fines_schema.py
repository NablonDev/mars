"""move every table from public into the fines schema

This service is becoming one domain inside a database shared with the
sibling cmir project, so `public` is reserved for the cross-domain tables
that project owns (agent registry/runs/traces, HITL actions) and everything
here moves into `fines` -- the schema `Base.metadata` is bound to
(app/db/base.py::FINES_SCHEMA). See docs/DATABASE.md.

`ALTER TABLE ... SET SCHEMA` is zero-copy and carries the table's owned
indexes, constraints and sequences with it. Foreign keys across the moved
tables stay valid because Postgres resolves FK targets by OID, not by
schema-qualified name -- all 14 tables move in one transaction anyway.

`downgrade()` moves everything back to `public` but deliberately does NOT
drop the `fines` schema: by the time anyone downgrades, something else may
own objects in it, and dropping a schema out from under another owner is a
far worse outcome than leaving an empty one behind.

SQLite has no schemas. app/db/session.py and alembic/env.py translate
`fines` away entirely on that backend, so both directions are a no-op
there rather than a syntax error -- the tables are already exactly where
the ORM expects them.

**Operational note:** on an existing database this migration has to run
*before* `alembic/env.py` starts asking for `fines.alembic_version`,
with a one-time manual move of the version table in between. The exact
sequence is in docs/RUNBOOK.md §3 "Moving an existing database into the
`fines` schema" -- it cannot be expressed safely inside a migration
script, because the script's own bookkeeping is what is being moved.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Hardcoded rather than imported from app.db.base.FINES_SCHEMA: a migration
# has to keep meaning what it meant when it was applied, so it must not
# change behaviour if that constant is ever renamed.
_FINES_SCHEMA = "fines"

# All 14 tables, under their post-0003 names. Listed explicitly rather than
# read off Base.metadata so this migration keeps describing the schema as
# it was at revision 0004, however the ORM evolves afterwards.
_TABLES: tuple[str, ...] = (
    "dim_retailer",
    "dim_sku",
    "dim_location",
    "dim_carrier",
    "dim_fine_rule",
    "dim_fine_rule_tier",
    "fact_order",
    "fact_order_confirmation",
    "fact_production_schedule",
    "fact_shipment",
    "fact_demand_exception",
    "fact_projected_fine",
    "fact_actual_fine",
    "fact_projection_explanation",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{_FINES_SCHEMA}"')
    for table in _TABLES:
        op.execute(f'ALTER TABLE public."{table}" SET SCHEMA "{_FINES_SCHEMA}"')


def downgrade() -> None:
    if not _is_postgres():
        return
    for table in reversed(_TABLES):
        op.execute(f'ALTER TABLE "{_FINES_SCHEMA}"."{table}" SET SCHEMA public')
