"""workflow_threads.pending_action_id from integer to uuid

Revision ID: e4b7c391a052
Revises: d9c2a745b810
Create Date: 2026-08-20 09:15:00.000000

`workflow_threads.pending_action_id` is an informal, FK-less reference to
`pending_human_actions.id` -- it was never a real database FK constraint, so
it was invisible to the FK-graph check that scoped d9c2a745b810 (which
converted `pending_human_actions.id` itself from integer to uuid) and was
left behind as `integer`, breaking the very first live HITL interrupt for
either agent (`CannotCoerce: cannot cast type uuid to integer`).

app/models/observability.py's WorkflowThreadORM.pending_action_id already
declares `Mapped[UUID | None] = mapped_column(UUID_PK)` to match.

Verified against the target Postgres instance immediately before writing
this revision that every existing workflow_threads row already has
pending_action_id = NULL (the legacy-data migration script never populated
this column), so this is a plain type change with nothing to remap --
same empty-value situation as d9c2a745b810.

Postgres-only, like d9c2a745b810's conversions: SQLite has no uuid type, and
tests/unit/db/test_migration_parity.py only compares table/column *names*,
never types.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b7c391a052"
down_revision: str | None = "d9c2a745b810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "cmir"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.alter_column(
        "workflow_threads",
        "pending_action_id",
        existing_type=sa.Integer(),
        type_=sa.Uuid(),
        schema=_SCHEMA,
        postgresql_using="NULL::uuid",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.alter_column(
        "workflow_threads",
        "pending_action_id",
        existing_type=sa.Uuid(),
        type_=sa.Integer(),
        schema=_SCHEMA,
        postgresql_using="NULL::integer",
    )
