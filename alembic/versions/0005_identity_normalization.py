"""Format-insensitive CMIR identity matching.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-17

"Cust-9900", "cust-9900", "cust9900", and "CUST-9900" must all resolve to the
same entity. Raw customer_identity/target_customer_material_ref columns stay
exactly as submitted (display, audit); these two key columns drive lookup and
uniqueness instead. Application code populates them at write time via
app.services.identity.normalize_identity_key -- keep that Python regex and
the SQL REGEXP_REPLACE below in sync by inspection if this rule ever changes.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE cmir_records
            ADD COLUMN IF NOT EXISTS customer_identity_key TEXT,
            ADD COLUMN IF NOT EXISTS target_customer_material_ref_key TEXT
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE cmir_records
            SET customer_identity_key = UPPER(REGEXP_REPLACE(customer_identity, '[^A-Za-z0-9]', '', 'g')),
                target_customer_material_ref_key = UPPER(REGEXP_REPLACE(target_customer_material_ref, '[^A-Za-z0-9]', '', 'g'))
            WHERE customer_identity_key IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE cmir_records
            ALTER COLUMN customer_identity_key SET NOT NULL,
            ALTER COLUMN target_customer_material_ref_key SET NOT NULL
            """
        )
    )

    op.execute(sa.text("DROP INDEX IF EXISTS idx_cmir_records_one_current_per_entity"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cmir_records_one_current_per_entity "
            "ON cmir_records (customer_identity_key, target_customer_material_ref_key) WHERE is_current"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_cmir_records_one_current_per_entity"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cmir_records_one_current_per_entity "
            "ON cmir_records (customer_identity, target_customer_material_ref) WHERE is_current"
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE cmir_records
            DROP COLUMN IF EXISTS customer_identity_key,
            DROP COLUMN IF EXISTS target_customer_material_ref_key
            """
        )
    )
