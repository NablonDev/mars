"""add fact_mitigation_input

Revision ID: 418499fefe8e
Revises: e4b7c391a052
Create Date: 2026-08-20 05:31:50.154456

Hand-reviewed after autogenerate: the raw autogenerate output also proposed
dropping the `checkpoint_blobs`/`checkpoints`/`checkpoint_writes`/
`checkpoint_migrations` tables (created directly by
langgraph-checkpoint-postgres against the live DB, never part of
Base.metadata -- not this project's tables to manage) and dropping
`uq_job_item_inflight` (the documented autogenerate false-positive: a
partial index that only exists as raw DDL in ff84c023d5e9, deliberately not
expressible on the ORM model -- see app/models/job_queue.py::JobItem's
docstring). Both are unrelated noise from comparing a live dev database
against Base.metadata; removed from this revision, which contains only the
actual schema change: adding fact_mitigation_input.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "418499fefe8e"
down_revision: str | None = "e4b7c391a052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fact_mitigation_input",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=30), nullable=False),
        sa.Column("shortage_cause", sa.String(length=20), nullable=False),
        sa.Column("shortage_cause_confirmed", sa.Boolean(), nullable=False),
        sa.Column("capacity_boost_cost_per_unit", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("capacity_boost_max_units_per_day", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("capacity_boost_data_confirmed", sa.Boolean(), nullable=False),
        sa.Column("express_carrier_cost", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("express_carrier_transit_days", sa.Integer(), nullable=True),
        sa.Column("express_carrier_data_confirmed", sa.Boolean(), nullable=False),
        sa.Column("split_shipment_handling_cost", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["fines.fact_order.order_id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        "ix_fact_mitigation_input_order_id",
        "fact_mitigation_input",
        ["order_id"],
        unique=True,
        schema="fines",
    )


def downgrade() -> None:
    op.drop_index("ix_fact_mitigation_input_order_id", table_name="fact_mitigation_input", schema="fines")
    op.drop_table("fact_mitigation_input", schema="fines")
