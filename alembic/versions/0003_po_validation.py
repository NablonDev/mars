"""PO Validation Agent (PRD Revision 3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

Reuses agent_runs / workflow_threads / pending_human_actions / hitl_actions
across both agents (email CMIR + PO validation). A PO-triggered row has no
source email, so email_id becomes nullable everywhere it was previously
required, and each table gains a nullable po_line_id counterpart. See
migrations/schema.sql's original comment for the cmir_records.email_id
nullability deviation from PRD §4.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE cmir_records ALTER COLUMN email_id DROP NOT NULL"))

    op.execute(sa.text("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS po_line_id UUID"))
    op.execute(sa.text("ALTER TABLE workflow_threads ADD COLUMN IF NOT EXISTS po_line_id UUID"))
    op.execute(sa.text("ALTER TABLE workflow_threads ALTER COLUMN email_id DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE pending_human_actions ADD COLUMN IF NOT EXISTS po_line_id UUID"))
    op.execute(sa.text("ALTER TABLE pending_human_actions ALTER COLUMN email_id DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE hitl_actions ADD COLUMN IF NOT EXISTS po_line_id UUID"))

    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_agent_runs_po_line_id ON agent_runs(po_line_id)"))
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_workflow_threads_po_line_id ON workflow_threads(po_line_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_pending_human_actions_po_line_id "
            "ON pending_human_actions(po_line_id)"
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_hitl_actions_po_line_id ON hitl_actions(po_line_id)"))

    # One row per PO line under validation. `plant` is required here even
    # though the PRD leaves plant-resolution as an open question (§14) --
    # this agent takes it as ingest input rather than resolving it itself.
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS po_lines (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id TEXT NOT NULL,
                po_number TEXT NOT NULL,
                po_line_number TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                customer_material_code TEXT NOT NULL,
                plant TEXT NOT NULL,
                order_quantity NUMERIC NOT NULL,
                uom TEXT,
                requested_delivery_date DATE,
                raw_payload JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'NEW',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_po_lines_batch_id ON po_lines(batch_id)"))
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS idx_po_lines_status ON po_lines(status, updated_at DESC)")
    )

    # Local mirror of the SAP MARC fields this agent needs. Keyed by
    # (sap_material_number, plant) to match MARC's real grain.
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS material_master (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                sap_material_number TEXT NOT NULL,
                plant TEXT NOT NULL,
                description TEXT,
                available_quantity NUMERIC NOT NULL DEFAULT 0,
                uom TEXT,
                discontinuation_indicator TEXT,
                effective_out_date DATE,
                follow_up_material_number TEXT,
                last_synced_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (sap_material_number, plant)
            )
            """
        )
    )

    # One row per validation/processing failure on a PO line. Distinct
    # from hitl_actions, which records human decisions, not system faults.
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS po_line_errors (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                po_line_id UUID NOT NULL REFERENCES po_lines(id),
                agent_run_id INTEGER REFERENCES agent_runs(id),
                error_type TEXT NOT NULL,
                error_code TEXT,
                error_message TEXT,
                node_name TEXT,
                raw_error_detail JSONB,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved BOOLEAN NOT NULL DEFAULT FALSE,
                resolved_at TIMESTAMPTZ,
                resolved_by TEXT
            )
            """
        )
    )

    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS idx_po_line_errors_po_line_id ON po_line_errors(po_line_id)")
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS po_line_errors"))
    op.execute(sa.text("DROP TABLE IF EXISTS material_master"))
    op.execute(sa.text("DROP TABLE IF EXISTS po_lines"))
    op.execute(sa.text("ALTER TABLE hitl_actions DROP COLUMN IF EXISTS po_line_id"))
    op.execute(sa.text("ALTER TABLE pending_human_actions DROP COLUMN IF EXISTS po_line_id"))
    op.execute(sa.text("ALTER TABLE workflow_threads DROP COLUMN IF EXISTS po_line_id"))
    op.execute(sa.text("ALTER TABLE agent_runs DROP COLUMN IF EXISTS po_line_id"))
    # email_id/cmir_records.email_id nullability is intentionally left as-is on
    # downgrade -- re-adding NOT NULL would fail against any PO-triggered rows
    # already written with a NULL email_id.
