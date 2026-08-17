"""Bootstrap base tables assumed pre-existing by the original migrations/schema.sql.

Revision ID: 0001
Revises:
Create Date: 2026-08-17

migrations/schema.sql itself carried a TODO ("assumes the pre-existing base
tables already exist ... the authoritative pre-PRD base schema is not
documented in the repo") -- this revision is that missing base, reconstructed
from the only two sources of truth available: (a) columns schema.sql never
ALTERs (so they must already exist) and (b) columns schema.sql only adds a
DEFAULT to (so the column exists, just without one yet).

IMPORTANT for already-deployed environments: their base tables already exist
with real data. Do NOT run `alembic upgrade` from scratch there -- instead
run `alembic stamp 0001` (or `alembic stamp head` if 0002-0005 have also
already been applied via the old schema.sql) so Alembic's revision tracking
starts from the correct point without re-running DDL against live tables.
This revision is only meant to bring a genuinely fresh database (local dev,
CI, a new environment) up to the same starting line.

CREATE TABLE IF NOT EXISTS is used deliberately (matching the original raw
SQL's own idempotency style) so this revision tolerates re-running against a
partially-bootstrapped fresh database.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS email_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                sender TEXT,
                subject TEXT,
                raw_content TEXT,
                extracted_json JSON,
                missing_fields JSON,
                status TEXT,
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id SERIAL PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'running',
                current_node TEXT,
                started_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                error TEXT
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS cmir_records (
                id SERIAL PRIMARY KEY,
                email_id UUID NOT NULL,
                sender_type TEXT,
                customer_identity TEXT,
                material_identity TEXT,
                intent_phrase TEXT,
                existing_cmir_ref TEXT,
                brand TEXT,
                site TEXT,
                target_grd_code TEXT,
                target_customer_material_ref TEXT,
                effective_date DATE,
                reason TEXT,
                approved_at TIMESTAMPTZ
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS hitl_actions (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES agent_runs(id),
                email_id UUID,
                interrupt_type TEXT NOT NULL,
                question JSON NOT NULL,
                answer JSON NOT NULL,
                decision TEXT,
                reason TEXT,
                actor TEXT NOT NULL,
                responded_at TIMESTAMPTZ
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS agent_trace (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES agent_runs(id),
                node_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                completed_at TIMESTAMPTZ NOT NULL,
                duration_ms INTEGER NOT NULL,
                input_snapshot JSON,
                output_snapshot JSON,
                error TEXT
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS email_action_log (
                id SERIAL PRIMARY KEY,
                email_id UUID NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details JSON NOT NULL
            )
            """
        )
    )


def downgrade() -> None:
    # Reversing this would drop the base tables the rest of the schema depends
    # on -- not meaningful as a downgrade step for a bootstrap revision.
    raise NotImplementedError("0001_base_tables is a bootstrap revision and is not reversible.")
