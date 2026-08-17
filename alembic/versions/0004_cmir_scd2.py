"""SCD Type 2 CMIR versioning (see docs/memory.md, docs/flow.md #2/#4).

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17

cmir_records moves from "append-only insert log" to a proper SCD2 history:
exactly one is_current=true row per (customer_identity,
target_customer_material_ref), every prior version kept with valid_to set and
superseded_by_id pointing forward.

The partial unique index created here is the actual correctness guarantee for
"only one active record per entity" -- enforced by Postgres itself (via a
caught IntegrityError at insert time, see app.repositories.cmir), not just by
application-level checks. The ROW_NUMBER() OVER (PARTITION BY ...) backfill
has no declarative Alembic equivalent and is kept as raw SQL, exactly as in
the original migrations/schema.sql.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE cmir_records
            ADD COLUMN IF NOT EXISTS is_current BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS superseded_by_id INTEGER REFERENCES cmir_records(id)
            """
        )
    )

    # The base schema already had an unused approved_at column meaning almost
    # exactly what SCD2's valid_from needs: the moment this row became the
    # record of truth. persist_cmir is the only place a version is ever
    # created, and it only ever runs immediately after approval, so
    # "approved" and "became current" are the same instant in this design --
    # reuse the column instead of adding a second one for the same fact.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'cmir_records' AND column_name = 'approved_at'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'cmir_records' AND column_name = 'valid_from'
                ) THEN
                    ALTER TABLE cmir_records RENAME COLUMN approved_at TO valid_from;
                END IF;
            END $$
            """
        )
    )

    # Environments without the pre-existing approved_at column (or a second
    # run of this migration) get valid_from added directly; a no-op once it
    # exists either way it got there.
    op.execute(sa.text("ALTER TABLE cmir_records ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ"))

    # Backfill valid_from for rows that predate this migration and never had a
    # real approved_at either -- these get a synthetic valid_from of "now."
    # Acceptable because their relative history ordering doesn't depend on
    # this value, only the is_current flag (backfilled next) does.
    op.execute(sa.text("UPDATE cmir_records SET valid_from = NOW() WHERE valid_from IS NULL"))

    op.execute(
        sa.text(
            """
            ALTER TABLE cmir_records
            ALTER COLUMN valid_from SET NOT NULL,
            ALTER COLUMN valid_from SET DEFAULT NOW()
            """
        )
    )

    # Backfill is_current: for every (customer_identity,
    # target_customer_material_ref) pair that doesn't already have a current
    # row (idempotent -- a pair already marked current is left untouched),
    # mark the highest-id row as current. id DESC is used as the recency
    # proxy for pre-migration data.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    customer_identity,
                    target_customer_material_ref,
                    ROW_NUMBER() OVER (
                        PARTITION BY customer_identity, target_customer_material_ref
                        ORDER BY id DESC
                    ) AS rn
                FROM cmir_records
                WHERE customer_identity IS NOT NULL
                  AND target_customer_material_ref IS NOT NULL
            ),
            already_current AS (
                SELECT DISTINCT customer_identity, target_customer_material_ref
                FROM cmir_records
                WHERE is_current
            )
            UPDATE cmir_records
            SET is_current = TRUE
            FROM ranked
            WHERE cmir_records.id = ranked.id
              AND ranked.rn = 1
              AND NOT EXISTS (
                  SELECT 1 FROM already_current ac
                  WHERE ac.customer_identity = ranked.customer_identity
                    AND ac.target_customer_material_ref = ranked.target_customer_material_ref
              )
            """
        )
    )

    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cmir_records_one_current_per_entity "
            "ON cmir_records (customer_identity, target_customer_material_ref) WHERE is_current"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_cmir_records_superseded_by_id "
            "ON cmir_records(superseded_by_id)"
        )
    )


def downgrade() -> None:
    # Schema-shape reversal only -- the is_current/valid_from backfills above
    # are not reversible (the pre-SCD2 state they're derived from is gone).
    op.execute(sa.text("DROP INDEX IF EXISTS idx_cmir_records_superseded_by_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_cmir_records_one_current_per_entity"))
    op.execute(
        sa.text(
            """
            ALTER TABLE cmir_records
            DROP COLUMN IF EXISTS is_current,
            DROP COLUMN IF EXISTS valid_to,
            DROP COLUMN IF EXISTS superseded_by_id
            """
        )
    )
    # valid_from / approved_at rename is intentionally left alone on downgrade
    # -- reversing it would require knowing whether the pre-migration
    # environment ever had a populated approved_at, which this revision
    # cannot reconstruct.
