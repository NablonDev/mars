"""rename dim_penalty_rule -> dim_fine_rule, dim_penalty_rule_tier -> dim_fine_rule_tier

Catches the database up to the ORM rename ("penalty" -> "fine" everywhere;
app/models/fine_rule.py). Deliberately a *new* migration rather than an
edit to 0001_initial_schema.py: 0001 has already been applied to real
databases, and rewriting an applied revision would leave those databases
silently disagreeing with their own recorded history.

`op.rename_table` is metadata-only on Postgres and, since SQLite 3.25,
also rewrites the referencing FK definitions in other tables, so the two
foreign keys pointing at these tables (dim_fine_rule_tier.rule_id and
fact_projected_fine.rule_id) survive untouched on both backends --
Postgres tracks FK targets by OID, not by name.

Indexes are *not* carried along by a table rename on Postgres, so the two
explicitly named ones are renamed here as well. Postgres-generated
constraint names (`dim_penalty_rule_pkey`, `dim_penalty_rule_rule_id_key`,
`dim_penalty_rule_tier_rule_id_fkey`, and the per-column NOT NULL entries
Postgres 18 records in pg_constraint) still carry the old prefix after
this migration -- cosmetic, but it does surface in constraint-violation
error messages. Renaming those was left out of scope on purpose: the exact
set of auto-generated names varies by Postgres version, so a migration
hardcoding them would be both fragile and incomplete.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-11
"""

from collections.abc import Sequence
from dataclasses import dataclass

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


@dataclass(frozen=True)
class _TableRename:
    old_table: str
    new_table: str
    old_index: str
    new_index: str
    index_column: str


_RENAMES: tuple[_TableRename, ...] = (
    _TableRename(
        old_table="dim_penalty_rule",
        new_table="dim_fine_rule",
        old_index="ix_dim_penalty_rule_rule_id",
        new_index="ix_dim_fine_rule_rule_id",
        index_column="rule_id",
    ),
    _TableRename(
        old_table="dim_penalty_rule_tier",
        new_table="dim_fine_rule_tier",
        old_index="ix_dim_penalty_rule_tier_tier_id",
        new_index="ix_dim_fine_rule_tier_tier_id",
        index_column="tier_id",
    ),
)


def _rename_index(from_name: str, to_name: str, table_name: str, column: str) -> None:
    """`ALTER INDEX ... RENAME TO ...` on Postgres -- metadata-only, no
    rebuild. Every other backend (SQLite, in tests/test_migration_parity.py)
    has no ALTER INDEX at all, so drop and recreate instead; equivalent
    outcome, and free at this data volume."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER INDEX "{from_name}" RENAME TO "{to_name}"')
    else:
        op.drop_index(from_name, table_name=table_name)
        op.create_index(to_name, table_name, [column])


def upgrade() -> None:
    for rename in _RENAMES:
        op.rename_table(rename.old_table, rename.new_table)
        _rename_index(rename.old_index, rename.new_index, rename.new_table, rename.index_column)


def downgrade() -> None:
    for rename in reversed(_RENAMES):
        op.rename_table(rename.new_table, rename.old_table)
        _rename_index(rename.new_index, rename.old_index, rename.old_table, rename.index_column)
