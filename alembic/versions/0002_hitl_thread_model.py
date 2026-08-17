"""CMIR HITL batch_id + per-email agent_run_id thread model.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17

Ports migrations/schema.sql's "CMIR HITL batch_id + per-email agent_run_id
thread model" section verbatim (as raw SQL via op.execute) rather than
re-expressing it through declarative op.* calls: this migration mixes schema
changes with data backfills (the email_events queue_status backfill, the
workflow_threads/pending_human_actions batch_id backfill) and a conditional
DO $$ block, none of which have a clean declarative equivalent, and keeping
the exact original SQL removes any risk of a subtle behavioral drift during
the flatten-to-app/ restructure.

CREATE TABLE/ADD COLUMN/CREATE INDEX keep their original IF NOT EXISTS guards
(matching the source SQL's own re-run tolerance) rather than dropping them --
this costs nothing under Alembic's own revision tracking and adds a safety
net if this revision is ever re-applied against a partially-migrated
database.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE email_events ADD COLUMN IF NOT EXISTS source_message_id TEXT"))

    op.execute(
        sa.text(
            """
            ALTER TABLE email_events
            ADD COLUMN IF NOT EXISTS source_imap_id TEXT,
            ADD COLUMN IF NOT EXISTS queue_status TEXT NOT NULL DEFAULT 'new',
            ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS queue_message_id TEXT,
            ADD COLUMN IF NOT EXISTS queue_delivery_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS queue_error TEXT
            """
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE email_events
            ALTER COLUMN created_at SET DEFAULT NOW(),
            ALTER COLUMN updated_at SET DEFAULT NOW()
            """
        )
    )

    # Existing email_events predate the queue lifecycle and must not be picked up
    # by the Service Bus enqueuer after this migration is applied.
    op.execute(
        sa.text(
            """
            UPDATE email_events
            SET queue_status = 'processed',
                processed_at = COALESCE(processed_at, updated_at, NOW())
            WHERE queue_status = 'new'
              AND queue_message_id IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_events_source_message_id "
            "ON email_events(source_message_id) WHERE source_message_id IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_email_events_queue_status_created_at "
            "ON email_events(queue_status, created_at ASC)"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_events_queue_message_id "
            "ON email_events(queue_message_id) WHERE queue_message_id IS NOT NULL"
        )
    )

    # agent_runs now represents one per-email agent execution. batch_id groups
    # all agent runs created by one POST /api/v1/ingest/emails request.
    op.execute(
        sa.text(
            """
            ALTER TABLE agent_runs
            ADD COLUMN IF NOT EXISTS batch_id TEXT,
            ADD COLUMN IF NOT EXISTS thread_id TEXT,
            ADD COLUMN IF NOT EXISTS email_id UUID,
            ADD COLUMN IF NOT EXISTS run_type TEXT NOT NULL DEFAULT 'email_ingest',
            ADD COLUMN IF NOT EXISTS total_threads INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS completed_threads INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS waiting_threads INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS failed_threads INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS metadata JSON NOT NULL DEFAULT '{}'::json
            """
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE agent_runs
            ALTER COLUMN started_at SET DEFAULT NOW(),
            ALTER COLUMN updated_at SET DEFAULT NOW()
            """
        )
    )

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'agent_runs'
                      AND column_name = 'thread_id'
                ) THEN
                    ALTER TABLE agent_runs ALTER COLUMN thread_id DROP NOT NULL;
                END IF;
            END $$
            """
        )
    )

    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_agent_runs_batch_id ON agent_runs(batch_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_agent_runs_thread_id ON agent_runs(thread_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_agent_runs_email_id ON agent_runs(email_id)"))
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status, updated_at DESC)")
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS workflow_threads (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id TEXT NOT NULL,
                agent_run_id INTEGER NOT NULL REFERENCES agent_runs(id),
                thread_id TEXT UNIQUE NOT NULL,
                email_id UUID NOT NULL REFERENCES email_events(id),
                source_message_id TEXT,
                sender TEXT,
                subject TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                current_node TEXT,
                stage TEXT NOT NULL DEFAULT 'INGESTING',
                cmir_status TEXT,
                latest_snapshot JSON,
                pending_action_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                error TEXT
            )
            """
        )
    )

    op.execute(sa.text("ALTER TABLE workflow_threads ADD COLUMN IF NOT EXISTS batch_id TEXT"))
    op.execute(
        sa.text("UPDATE workflow_threads SET batch_id = 'batch_legacy' WHERE batch_id IS NULL")
    )
    op.execute(sa.text("ALTER TABLE workflow_threads ALTER COLUMN batch_id SET NOT NULL"))

    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS idx_workflow_threads_batch_id ON workflow_threads(batch_id)")
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_workflow_threads_agent_run_id ON workflow_threads(agent_run_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_workflow_threads_status ON workflow_threads(status, updated_at DESC)"
        )
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS idx_workflow_threads_email_id ON workflow_threads(email_id)")
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS pending_human_actions (
                id SERIAL PRIMARY KEY,
                batch_id TEXT NOT NULL,
                agent_run_id INTEGER NOT NULL REFERENCES agent_runs(id),
                thread_id TEXT NOT NULL REFERENCES workflow_threads(thread_id),
                email_id UUID NOT NULL REFERENCES email_events(id),
                interrupt_type TEXT NOT NULL,
                payload JSONB NOT NULL,
                state_snapshot JSON NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                answer JSONB,
                actor TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            )
            """
        )
    )

    op.execute(sa.text("ALTER TABLE pending_human_actions ADD COLUMN IF NOT EXISTS batch_id TEXT"))
    op.execute(
        sa.text("UPDATE pending_human_actions SET batch_id = 'batch_legacy' WHERE batch_id IS NULL")
    )
    op.execute(sa.text("ALTER TABLE pending_human_actions ALTER COLUMN batch_id SET NOT NULL"))

    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_human_actions_one_open_per_thread "
            "ON pending_human_actions(thread_id) WHERE status = 'open'"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_pending_human_actions_batch_id ON pending_human_actions(batch_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_pending_human_actions_agent_run_id "
            "ON pending_human_actions(agent_run_id)"
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE hitl_actions
            ADD COLUMN IF NOT EXISTS batch_id TEXT,
            ADD COLUMN IF NOT EXISTS thread_id TEXT,
            ADD COLUMN IF NOT EXISTS action_type TEXT,
            ADD COLUMN IF NOT EXISTS field_changes JSON
            """
        )
    )

    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_hitl_actions_batch_id ON hitl_actions(batch_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_hitl_actions_thread_id ON hitl_actions(thread_id)"))

    op.execute(
        sa.text(
            "ALTER TABLE agent_trace ADD COLUMN IF NOT EXISTS batch_id TEXT, "
            "ADD COLUMN IF NOT EXISTS thread_id TEXT"
        )
    )

    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_agent_trace_batch_id ON agent_trace(batch_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_agent_trace_thread_id ON agent_trace(thread_id)"))


def downgrade() -> None:
    # Schema-shape reversal only -- the queue_status/batch_id data backfills
    # above are not reversible (the original blank values are gone).
    op.execute(sa.text("DROP TABLE IF EXISTS pending_human_actions"))
    op.execute(sa.text("DROP TABLE IF EXISTS workflow_threads"))
    op.execute(
        sa.text(
            "ALTER TABLE agent_trace DROP COLUMN IF EXISTS batch_id, DROP COLUMN IF EXISTS thread_id"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE hitl_actions DROP COLUMN IF EXISTS batch_id, DROP COLUMN IF EXISTS thread_id, "
            "DROP COLUMN IF EXISTS action_type, DROP COLUMN IF EXISTS field_changes"
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE agent_runs
            DROP COLUMN IF EXISTS batch_id,
            DROP COLUMN IF EXISTS thread_id,
            DROP COLUMN IF EXISTS email_id,
            DROP COLUMN IF EXISTS run_type,
            DROP COLUMN IF EXISTS total_threads,
            DROP COLUMN IF EXISTS completed_threads,
            DROP COLUMN IF EXISTS waiting_threads,
            DROP COLUMN IF EXISTS failed_threads,
            DROP COLUMN IF EXISTS metadata
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE email_events
            DROP COLUMN IF EXISTS source_message_id,
            DROP COLUMN IF EXISTS source_imap_id,
            DROP COLUMN IF EXISTS queue_status,
            DROP COLUMN IF EXISTS queued_at,
            DROP COLUMN IF EXISTS processing_started_at,
            DROP COLUMN IF EXISTS processed_at,
            DROP COLUMN IF EXISTS queue_message_id,
            DROP COLUMN IF EXISTS queue_delivery_count,
            DROP COLUMN IF EXISTS queue_error
            """
        )
    )
