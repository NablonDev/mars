"""derive job queue check constraints from enums

Revision ID: 72e1a33a436e
Revises: ff84c023d5e9
Create Date: 2026-08-15 20:24:11.950201

Data-wise a no-op: the permitted `job_run.run_type` / `job_item.status` /
`job_item.task_type` values are unchanged. Only the generated DDL text
changes, because `app/models/job_queue.py` now builds each CheckConstraint
from `app.models.enums` (`JobRunType`/`JobItemStatus`/`JobTaskType`) with
values sorted alphabetically, rather than as a second, independently
hand-maintained SQL string -- see that module's `_check_in_sql` for why the
sort is required (StrEnum iteration order is stable, but nothing here may
depend on that by accident). `ck_job_item_task_type`'s value list happens
to already be alphabetical (`ORDER_RUN` < `SUMMARY_REGEN`), so its own text
is byte-identical before and after; it is still dropped and recreated here
for symmetry with the other two, and so this migration is not silently
incomplete if a future task_type value ever sorted differently.

Uses `op.batch_alter_table` (not a bare `op.drop_constraint`/
`op.create_check_constraint`) so this migration also runs on SQLite, the
dialect `tests/unit/db/test_migration_parity.py` exercises it against --
SQLite has no `ALTER TABLE ... DROP/ADD CONSTRAINT` support at all, so a
non-batch constraint swap would only ever work on Postgres. On Postgres,
batch mode still compiles down to plain `ALTER TABLE ... DROP CONSTRAINT`
/ `ADD CONSTRAINT` (no table rebuild) -- the recreate-via-temp-table path
is a SQLite-only behavior of `op.batch_alter_table`.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "72e1a33a436e"
down_revision: str | None = "ff84c023d5e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Old/new CHECK-constraint SQL, both spelled out literally here (not
# imported from app.models.job_queue._check_in_sql/app.models.enums) --
# an Alembic migration must keep working unchanged even if a future
# refactor changes how those are generated or what they currently contain.
_OLD_RUN_TYPE_SQL = "run_type IN ('SCHEDULED_DAILY', 'MANUAL_BATCH', 'ON_DEMAND')"
_NEW_RUN_TYPE_SQL = "run_type IN ('MANUAL_BATCH', 'ON_DEMAND', 'SCHEDULED_DAILY')"

_OLD_STATUS_SQL = "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'DEAD')"
_NEW_STATUS_SQL = "status IN ('DEAD', 'PENDING', 'RUNNING', 'SUCCEEDED')"

# Unchanged text -- ORDER_RUN already sorts before SUMMARY_REGEN -- but
# still dropped/recreated below for symmetry (see module docstring).
_TASK_TYPE_SQL = "task_type IN ('ORDER_RUN', 'SUMMARY_REGEN')"


def _schema() -> str | None:
    """None on SQLite, 'fines' on Postgres -- same no-op-on-SQLite gating
    as `ff84c023d5e9`'s own `_add_column_schema` (see that migration for
    why: SQLite's batch/DDL rendering doesn't go through the connection's
    `schema_translate_map`, so a schema-qualified name here would produce
    'fines.job_run', a table SQLite has never heard of)."""
    return None if op.get_bind().dialect.name == "sqlite" else "fines"


def upgrade() -> None:
    schema = _schema()

    with op.batch_alter_table("job_run", schema=schema) as batch_op:
        batch_op.drop_constraint("ck_job_run_run_type", type_="check")
        batch_op.create_check_constraint("ck_job_run_run_type", _NEW_RUN_TYPE_SQL)

    with op.batch_alter_table("job_item", schema=schema) as batch_op:
        batch_op.drop_constraint("ck_job_item_status", type_="check")
        batch_op.create_check_constraint("ck_job_item_status", _NEW_STATUS_SQL)
        batch_op.drop_constraint("ck_job_item_task_type", type_="check")
        batch_op.create_check_constraint("ck_job_item_task_type", _TASK_TYPE_SQL)


def downgrade() -> None:
    schema = _schema()

    with op.batch_alter_table("job_item", schema=schema) as batch_op:
        batch_op.drop_constraint("ck_job_item_task_type", type_="check")
        batch_op.create_check_constraint("ck_job_item_task_type", _TASK_TYPE_SQL)
        batch_op.drop_constraint("ck_job_item_status", type_="check")
        batch_op.create_check_constraint("ck_job_item_status", _OLD_STATUS_SQL)

    with op.batch_alter_table("job_run", schema=schema) as batch_op:
        batch_op.drop_constraint("ck_job_run_run_type", type_="check")
        batch_op.create_check_constraint("ck_job_run_run_type", _OLD_RUN_TYPE_SQL)
