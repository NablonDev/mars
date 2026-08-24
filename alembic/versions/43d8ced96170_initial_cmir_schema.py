"""Initial cmir schema.

Revision ID: 43d8ced96170
Revises: e803d9470f31
Create Date: 2026-08-23

Squashes the previous cmir migration history into a single migration,
capturing the final schema as defined by Base.metadata.

The migration is chained after the fines schema squash to preserve a
single linear Alembic history. This is a history choice only; the cmir
schema does not depend on any fines tables.

The final schema includes the accumulated changes from the replaced
migrations, including bounded String columns, PostgreSQL JSONB types,
standard audit timestamps, SCD2 fields for cmir_records, required foreign
keys, and UUID-based identifiers/references.

Column widths and text types were reviewed and calibrated as part of this
squash rather than carried forward as separate migrations. Corresponding
API max_length constraints are maintained in app/schemas/.

LangGraph checkpoint tables are intentionally excluded because they are
created and managed directly by langgraph-checkpoint-postgres rather than
through Base.metadata.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "43d8ced96170"
down_revision: str | None = "e803d9470f31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("source_message_id", sa.Text(), nullable=True),
        sa.Column("source_imap_id", sa.String(length=100), nullable=True),
        sa.Column(
            "extracted_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "missing_fields",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("queue_status", sa.String(length=30), nullable=False),
        sa.Column("queue_message_id", sa.String(length=200), nullable=True),
        sa.Column("queue_error", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queue_delivery_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "material_master",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sap_material_number", sa.String(length=64), nullable=False),
        sa.Column("plant", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("available_quantity", sa.Numeric(), nullable=False),
        sa.Column("uom", sa.String(length=50), nullable=True),
        sa.Column("discontinuation_indicator", sa.String(length=30), nullable=True),
        sa.Column("effective_out_date", sa.Date(), nullable=True),
        sa.Column("follow_up_material_number", sa.String(length=64), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "po_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.String(length=100), nullable=False),
        sa.Column("po_number", sa.String(length=50), nullable=False),
        sa.Column("po_line_number", sa.String(length=50), nullable=False),
        sa.Column("customer_id", sa.String(length=50), nullable=False),
        sa.Column("customer_material_code", sa.String(length=50), nullable=False),
        sa.Column("plant", sa.String(length=30), nullable=False),
        sa.Column("order_quantity", sa.Numeric(), nullable=False),
        sa.Column("uom", sa.String(length=50), nullable=True),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column(
            "raw_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.String(length=100), nullable=True),
        sa.Column("thread_id", sa.String(length=100), nullable=True),
        sa.Column("email_id", sa.Uuid(), nullable=True),
        sa.Column("po_line_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("current_node", sa.String(length=100), nullable=True),
        sa.Column("run_type", sa.String(length=64), nullable=False),
        sa.Column("total_threads", sa.Integer(), nullable=False),
        sa.Column("completed_threads", sa.Integer(), nullable=False),
        sa.Column("waiting_threads", sa.Integer(), nullable=False),
        sa.Column("failed_threads", sa.Integer(), nullable=False),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["email_id"], ["cmir.email_events.id"]),
        sa.ForeignKeyConstraint(["po_line_id"], ["cmir.po_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "cmir_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email_id", sa.Uuid(), nullable=True),
        sa.Column("sender_type", sa.String(length=100), nullable=False),
        sa.Column("customer_identity", sa.String(length=255), nullable=False),
        sa.Column("material_identity", sa.String(length=255), nullable=False),
        sa.Column("intent_phrase", sa.Text(), nullable=True),
        sa.Column("existing_cmir_ref", sa.String(length=255), nullable=False),
        sa.Column("brand", sa.String(length=100), nullable=False),
        sa.Column("site", sa.String(length=100), nullable=False),
        sa.Column("target_grd_code", sa.String(length=255), nullable=False),
        sa.Column("target_customer_material_ref", sa.String(length=255), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("customer_identity_key", sa.String(length=255), nullable=False),
        sa.Column("target_customer_material_ref_key", sa.String(length=255), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["email_id"], ["cmir.email_events.id"]),
        sa.ForeignKeyConstraint(["superseded_by_id"], ["cmir.cmir_records.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "email_action_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("actor", sa.String(length=50), nullable=False),
        sa.Column(
            "details",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["email_id"], ["cmir.email_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "agent_traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.String(length=100), nullable=True),
        sa.Column("thread_id", sa.String(length=100), nullable=True),
        sa.Column("node_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "input_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "output_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["cmir.agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "hitl_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.String(length=100), nullable=True),
        sa.Column("thread_id", sa.String(length=100), nullable=True),
        sa.Column("email_id", sa.Uuid(), nullable=True),
        sa.Column("interrupt_type", sa.String(length=64), nullable=False),
        sa.Column(
            "question",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "answer",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=50), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("action_type", sa.String(length=64), nullable=True),
        sa.Column(
            "field_changes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("po_line_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["email_id"], ["cmir.email_events.id"]),
        sa.ForeignKeyConstraint(["po_line_id"], ["cmir.po_lines.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["cmir.agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "po_line_errors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("po_line_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("error_type", sa.String(length=50), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("node_name", sa.String(length=100), nullable=True),
        sa.Column(
            "raw_error_detail",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["cmir.agent_runs.id"]),
        sa.ForeignKeyConstraint(["po_line_id"], ["cmir.po_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )
    op.create_table(
        "workflow_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.String(length=100), nullable=False),
        sa.Column("batch_id", sa.String(length=100), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("email_id", sa.Uuid(), nullable=True),
        sa.Column("po_line_id", sa.Uuid(), nullable=True),
        sa.Column("source_message_id", sa.Text(), nullable=True),
        sa.Column("sender", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("current_node", sa.String(length=100), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("cmir_status", sa.String(length=64), nullable=True),
        sa.Column(
            "latest_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("pending_action_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["cmir.agent_runs.id"]),
        sa.ForeignKeyConstraint(["email_id"], ["cmir.email_events.id"]),
        sa.ForeignKeyConstraint(["po_line_id"], ["cmir.po_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),
        schema="cmir",
    )
    op.create_table(
        "pending_human_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.String(length=100), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.String(length=100), nullable=False),
        sa.Column("email_id", sa.Uuid(), nullable=True),
        sa.Column("po_line_id", sa.Uuid(), nullable=True),
        sa.Column("interrupt_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "state_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "answer",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("actor", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["cmir.agent_runs.id"]),
        sa.ForeignKeyConstraint(["email_id"], ["cmir.email_events.id"]),
        sa.ForeignKeyConstraint(["po_line_id"], ["cmir.po_lines.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["cmir.workflow_threads.thread_id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cmir",
    )


def downgrade() -> None:
    op.drop_table("pending_human_actions", schema="cmir")
    op.drop_table("workflow_threads", schema="cmir")
    op.drop_table("po_line_errors", schema="cmir")
    op.drop_table("hitl_actions", schema="cmir")
    op.drop_table("agent_traces", schema="cmir")
    op.drop_table("email_action_logs", schema="cmir")
    op.drop_table("cmir_records", schema="cmir")
    op.drop_table("agent_runs", schema="cmir")
    op.drop_table("po_lines", schema="cmir")
    op.drop_table("material_master", schema="cmir")
    op.drop_table("email_events", schema="cmir")
