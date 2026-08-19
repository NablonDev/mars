"""cmir: id columns on cmir_records/email_action_logs/hitl_actions/
pending_human_actions/agent_traces from integer to uuid

Revision ID: d9c2a745b810
Revises: ce704d36b4f3
Create Date: 2026-08-19 18:40:00.000000

app/models/cmir.py, observability.py, and email.py already declare
`id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True,
default=generate_uuid7)` for these five tables -- matching agent_runs,
which made this same move in an earlier migration. The live schema was
never brought in line: these five `id` columns are still
`integer`/serial, which is what scripts/ops/migrate_legacy_cmir_data.py's
DatatypeMismatch surfaced (it was written against the ORM models, which
were already correct).

Scope: only the `id` column on these five tables, plus the one column
anywhere in the schema that has a live FK constraint pointing at one of
those `id` columns -- cmir_records.superseded_by_id (self-referencing).
Checked the full FK graph in `cmir` (information_schema.table_constraints
+ key/constraint_column_usage): no other column -- in these tables or
elsewhere -- is FK-constrained against any of these five `id` columns.

Deliberately out of scope: cmir_records.email_id and
email_action_logs.email_id are already `uuid`-typed in the live schema
(set correctly by 8209afe73fa4) and the ORM now declares a
`ForeignKey(email_events.id)` on both that the live DB still lacks --
that's a different, pre-existing gap (missing constraint on an
already-correctly-typed column), not a type mismatch, and isn't touched
here.

Empty-table assumption: verified against the target Postgres instance
immediately before writing this revision that all five tables have zero
rows. On that basis, `id` values are simply regenerated with
`gen_random_uuid()` and `cmir_records.superseded_by_id` is reset to
NULL rather than remapped -- there is no existing data, and (per the FK
graph above) nothing else references these ids, so this loses nothing
here. This is NOT safe to reapply as-is against an environment where
these tables already hold real rows: regenerating `id` independently
per row would desynchronize any real cmir_records.superseded_by_id
supersede chain (old int pointers wouldn't match the new random UUIDs
assigned to other rows). A populated environment needs a proper
per-row id-map pass instead -- generate_uuid7() per existing row,
recorded in a dict, then used to rewrite superseded_by_id -- the same
approach scripts/ops/migrate_legacy_cmir_data.py already uses for
agent_runs.

Postgres-only, like ce704d36b4f3's type conversions: SQLite has no uuid
type, and tests/unit/db/test_migration_parity.py only compares table/
column *names* across the whole chain, never types, so there is nothing
for this migration to do there.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9c2a745b810"
down_revision: str | None = "ce704d36b4f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "cmir"

# table -> its id-backing sequence. email_action_logs' sequence keeps its
# pre-rename singular name ("email_action_log_id_seq") -- the table was
# renamed plural in 8209afe73fa4 but the sequence never was.
_ID_SEQUENCES: dict[str, str] = {
    "cmir_records": "cmir_records_id_seq",
    "email_action_logs": "email_action_log_id_seq",
    "hitl_actions": "hitl_actions_id_seq",
    "pending_human_actions": "pending_human_actions_id_seq",
    "agent_traces": "agent_traces_id_seq",
}

_SUPERSEDED_BY_FK = "cmir_records_superseded_by_id_fkey"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.drop_constraint(_SUPERSEDED_BY_FK, "cmir_records", schema=_SCHEMA, type_="foreignkey")

    op.alter_column(
        "cmir_records",
        "superseded_by_id",
        existing_type=sa.Integer(),
        type_=sa.Uuid(),
        schema=_SCHEMA,
        postgresql_using="NULL::uuid",
    )

    for table, seq in _ID_SEQUENCES.items():
        op.alter_column(table, "id", server_default=None, existing_type=sa.Integer(), schema=_SCHEMA)
        op.alter_column(
            table,
            "id",
            existing_type=sa.Integer(),
            type_=sa.Uuid(),
            schema=_SCHEMA,
            postgresql_using="gen_random_uuid()",
        )
        op.execute(f"DROP SEQUENCE IF EXISTS {_SCHEMA}.{seq}")

    op.create_foreign_key(
        _SUPERSEDED_BY_FK,
        "cmir_records",
        "cmir_records",
        ["superseded_by_id"],
        ["id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.drop_constraint(_SUPERSEDED_BY_FK, "cmir_records", schema=_SCHEMA, type_="foreignkey")

    for table, seq in reversed(list(_ID_SEQUENCES.items())):
        op.execute(f"CREATE SEQUENCE {_SCHEMA}.{seq}")
        op.alter_column(
            table,
            "id",
            existing_type=sa.Uuid(),
            type_=sa.Integer(),
            schema=_SCHEMA,
            postgresql_using=f"nextval('{_SCHEMA}.{seq}')",
        )
        op.execute(f"ALTER SEQUENCE {_SCHEMA}.{seq} OWNED BY {_SCHEMA}.{table}.id")
        op.alter_column(
            table,
            "id",
            existing_type=sa.Integer(),
            server_default=sa.text(f"nextval('{_SCHEMA}.{seq}'::regclass)"),
            schema=_SCHEMA,
        )

    op.alter_column(
        "cmir_records",
        "superseded_by_id",
        existing_type=sa.Uuid(),
        type_=sa.Integer(),
        schema=_SCHEMA,
        postgresql_using="NULL::integer",
    )

    op.create_foreign_key(
        _SUPERSEDED_BY_FK,
        "cmir_records",
        "cmir_records",
        ["superseded_by_id"],
        ["id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
    )
