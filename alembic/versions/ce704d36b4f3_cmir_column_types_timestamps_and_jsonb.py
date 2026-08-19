"""cmir column types, timestamps, jsonb, and missing FKs

Revision ID: ce704d36b4f3
Revises: 72e1a33a436e
Create Date: 2026-08-19 07:20:49.422910

Brings the cmir schema's column conventions in line with the fines side, in
one push since all four land together:

- bounded String(n) instead of unbounded Text for short/business-key fields
  (a handful stay Text -- free-text content with no natural bound: intent
  phrases, reasons, email/thread subjects, material descriptions).
- UUID_PK instead of postgresql.UUID(as_uuid=False) for FK-holding uuid
  columns -- no DDL for this part, both compile to the same native `uuid`
  type.
- the created_at/updated_at/deleted_at triplet on every table (cmir_records
  excluded -- its own SCD2 valid_from/valid_to/is_current already cover
  this).
- every remaining plain-json column moved to jsonb, matching the
  JSONB_OR_JSON convention already used by a handful of columns in these
  same tables (PendingHumanActionORM.payload/.answer, PoLineORM.raw_payload,
  PoLineErrorORM.raw_error_detail). None of these columns are ever queried
  with a json operator (grepped app/repositories, app/services -- always
  read/written as a whole Python dict via the ORM), so there's no reason
  left to keep the plain json representation over jsonb's smaller storage
  and indexing headroom.
- agent_runs.email_id/po_line_id and hitl_actions.email_id/po_line_id are
  now FK-constrained to email_events.id/po_lines.id -- previously
  UUID-typed but unconstrained, unlike the identically-shaped columns on
  workflow_threads/pending_human_actions. Each row only ever populates one
  of the two columns (a run is triggered by either an email or a PO line),
  but that's no obstacle to a FK on each independently: a FK only checks
  non-NULL values, so the always-NULL side of any given row is simply
  exempt. Confirmed no orphaned data existed before adding these.

See app/models/cmir.py, email.py, observability.py, po_validation.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ce704d36b4f3"
down_revision: str | None = "72e1a33a436e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, new length) for every Text/unbounded-String column being
# bounded. existing_type=Text() is used uniformly below even for
# agent_runs.status (a bare, unbounded String() before this migration) --
# Postgres casts both TEXT->VARCHAR(n) and VARCHAR->VARCHAR(n) implicitly, so
# this doesn't need its own branch.
_STRING_LENGTHS: list[tuple[str, str, int]] = [
    ("cmir_records", "sender_type", 30),
    ("cmir_records", "customer_identity", 150),
    ("cmir_records", "material_identity", 150),
    ("cmir_records", "existing_cmir_ref", 100),
    ("cmir_records", "brand", 100),
    ("cmir_records", "site", 50),
    ("cmir_records", "target_grd_code", 100),
    ("cmir_records", "target_customer_material_ref", 100),
    ("cmir_records", "customer_identity_key", 150),
    ("cmir_records", "target_customer_material_ref_key", 100),
    ("email_events", "sender", 320),
    ("email_events", "source_message_id", 500),
    ("email_events", "source_imap_id", 100),
    ("email_events", "status", 50),
    ("email_events", "queue_status", 30),
    ("email_events", "queue_message_id", 200),
    ("email_action_logs", "action", 100),
    ("email_action_logs", "actor", 150),
    ("agent_runs", "batch_id", 150),
    ("agent_runs", "thread_id", 150),
    ("agent_runs", "status", 50),
    ("agent_runs", "current_node", 150),
    ("agent_runs", "run_type", 50),
    ("workflow_threads", "thread_id", 150),
    ("workflow_threads", "batch_id", 150),
    ("workflow_threads", "source_message_id", 300),
    ("workflow_threads", "sender", 320),
    ("workflow_threads", "status", 50),
    ("workflow_threads", "current_node", 150),
    ("workflow_threads", "stage", 50),
    ("workflow_threads", "cmir_status", 50),
    ("pending_human_actions", "batch_id", 150),
    ("pending_human_actions", "thread_id", 150),
    ("pending_human_actions", "interrupt_type", 100),
    ("pending_human_actions", "status", 50),
    ("pending_human_actions", "actor", 50),
    ("agent_traces", "batch_id", 150),
    ("agent_traces", "thread_id", 150),
    ("agent_traces", "node_name", 150),
    ("agent_traces", "status", 50),
    ("hitl_actions", "batch_id", 150),
    ("hitl_actions", "interrupt_type", 100),
    ("hitl_actions", "decision", 50),
    ("hitl_actions", "reason", 300),
    ("hitl_actions", "actor", 50),
    ("hitl_actions", "thread_id", 150),
    ("hitl_actions", "action_type", 100),
    ("po_lines", "batch_id", 150),
    ("po_lines", "po_number", 50),
    ("po_lines", "po_line_number", 30),
    ("po_lines", "customer_id", 50),
    ("po_lines", "customer_material_code", 100),
    ("po_lines", "plant", 30),
    ("po_lines", "uom", 30),
    ("po_lines", "status", 50),
    ("material_master", "sap_material_number", 50),
    ("material_master", "plant", 30),
    ("material_master", "uom", 30),
    ("material_master", "discontinuation_indicator", 30),
    ("material_master", "follow_up_material_number", 100),
    ("po_line_errors", "error_type", 100),
    ("po_line_errors", "error_code", 100),
    ("po_line_errors", "node_name", 150),
    ("po_line_errors", "resolved_by", 150),
]

# (table, [new nullable timestamp columns]). cmir_records isn't here -- see
# module docstring.
_NEW_TIMESTAMP_COLUMNS: list[tuple[str, list[str]]] = [
    ("email_events", ["deleted_at"]),
    ("email_action_logs", ["created_at", "updated_at", "deleted_at"]),
    ("agent_runs", ["created_at", "deleted_at"]),
    ("workflow_threads", ["deleted_at"]),
    ("pending_human_actions", ["updated_at", "deleted_at"]),
    ("agent_traces", ["created_at", "updated_at", "deleted_at"]),
    ("hitl_actions", ["created_at", "updated_at", "deleted_at"]),
    ("po_lines", ["deleted_at"]),
    ("material_master", ["deleted_at"]),
    ("po_line_errors", ["created_at", "updated_at", "deleted_at"]),
]

# (table, column) for every plain-json column moving to jsonb.
_JSON_TO_JSONB: list[tuple[str, str]] = [
    ("email_events", "extracted_json"),
    ("email_events", "missing_fields"),
    ("email_action_logs", "details"),
    ("agent_runs", "metadata"),
    ("workflow_threads", "latest_snapshot"),
    ("pending_human_actions", "state_snapshot"),
    ("agent_traces", "input_snapshot"),
    ("agent_traces", "output_snapshot"),
    ("hitl_actions", "question"),
    ("hitl_actions", "answer"),
    ("hitl_actions", "field_changes"),
]

# (constraint name, table, referent table, column) for every FK being added.
_FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    ("fk_agent_runs_email_id_email_events", "agent_runs", "email_events", "email_id"),
    ("fk_agent_runs_po_line_id_po_lines", "agent_runs", "po_lines", "po_line_id"),
    ("fk_hitl_actions_email_id_email_events", "hitl_actions", "email_events", "email_id"),
    ("fk_hitl_actions_po_line_id_po_lines", "hitl_actions", "po_lines", "po_line_id"),
]


def _schema() -> str | None:
    """Schema to pass to op.add_column/op.drop_column/op.batch_alter_table
    for this migration.

    None on SQLite, 'cmir' on Postgres. Unlike op.create_table/op.create_index,
    ADD COLUMN/DROP COLUMN DDL -- and batch mode's table-recreate DDL -- for
    SQLite renders the schema-qualified table name directly rather than
    through the connection's schema_translate_map, so on SQLite (the dialect
    tests/test_migration_parity.py runs this migration against) a literal
    "cmir.table" fails with "no such table" even though the table exists
    unqualified.
    """
    return None if op.get_bind().dialect.name == "sqlite" else "cmir"


def _new_timestamp_column(name: str) -> sa.Column:
    if name == "deleted_at":
        return sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True)


def upgrade() -> None:
    # String(n)/jsonb conversions are Postgres-only: SQLite has no real
    # varchar length enforcement and no jsonb type (JSONB_OR_JSON's
    # with_variant already falls back to plain json there), and, like
    # add_column/drop_column, op.alter_column's DDL doesn't go through
    # schema_translate_map on SQLite either. tests/test_migration_parity.py
    # only compares table/column names, never types -- there is nothing for
    # this half to do on SQLite.
    if op.get_bind().dialect.name == "postgresql":
        for table, column, length in _STRING_LENGTHS:
            op.alter_column(
                table,
                column,
                existing_type=sa.Text(),
                type_=sa.String(length=length),
                schema="cmir",
            )
        # json->jsonb has no implicit cast, so each column needs an explicit USING.
        for table, column in _JSON_TO_JSONB:
            op.alter_column(
                table,
                column,
                existing_type=postgresql.JSON(astext_type=sa.Text()),
                type_=postgresql.JSONB(astext_type=sa.Text()),
                schema="cmir",
                postgresql_using=f"{column}::jsonb",
            )

    for table, columns in _NEW_TIMESTAMP_COLUMNS:
        for column in columns:
            op.add_column(table, _new_timestamp_column(column), schema=_schema())

    # Uses op.batch_alter_table (not bare op.create_foreign_key): SQLite has
    # no ALTER TABLE ... ADD CONSTRAINT, so this also runs on SQLite for
    # test_migration_parity.py. Constraints are explicitly named -- there's
    # no `fk` entry in Base.metadata's naming_convention (just `ix`), so
    # nothing resolves an autogenerated name for downgrade() to drop.
    schema = _schema()
    for source_table in ("agent_runs", "hitl_actions"):
        with op.batch_alter_table(source_table, schema=schema) as batch_op:
            for name, table, referent_table, column in _FOREIGN_KEYS:
                if table == source_table:
                    batch_op.create_foreign_key(
                        name, referent_table, [column], ["id"], referent_schema=schema
                    )


def downgrade() -> None:
    schema = _schema()
    for source_table in ("hitl_actions", "agent_runs"):
        with op.batch_alter_table(source_table, schema=schema) as batch_op:
            for name, table, _referent_table, _column in reversed(_FOREIGN_KEYS):
                if table == source_table:
                    batch_op.drop_constraint(name, type_="foreignkey")

    for table, columns in reversed(_NEW_TIMESTAMP_COLUMNS):
        for column in reversed(columns):
            op.drop_column(table, column, schema=_schema())

    if op.get_bind().dialect.name == "postgresql":
        for table, column in reversed(_JSON_TO_JSONB):
            op.alter_column(
                table,
                column,
                existing_type=postgresql.JSONB(astext_type=sa.Text()),
                type_=postgresql.JSON(astext_type=sa.Text()),
                schema="cmir",
                postgresql_using=f"{column}::json",
            )
        for table, column, _length in reversed(_STRING_LENGTHS):
            op.alter_column(
                table,
                column,
                existing_type=sa.String(),
                type_=sa.Text(),
                schema="cmir",
            )
