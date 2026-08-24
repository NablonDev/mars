"""Initial fines schema.

Revision ID: e803d9470f31
Revises:
Create Date: 2026-08-23

Squashes the previous fines schema migration history into a single initial
migration. All historical `dim_`/`fact_` table prefixes are removed and
tables are created directly with their final names; ORM class names remain
unchanged where required for compatibility.

DDL was generated from Base.metadata against an empty PostgreSQL database
and hand-reviewed. The final schema includes the accumulated changes from
the replaced migrations, including calibrated column types and widths,
constraints, foreign keys, and the MITIGATION_SUMMARY_REGEN task type.

The partial unique index `uq_job_item_inflight` is intentionally created as
raw DDL because it cannot be represented reliably through the ORM across
PostgreSQL and SQLite migration tests.

Column widths and text types were calibrated as part of this squash rather
than carried forward as separate migrations. Corresponding application-level
validation remains in the existing schemas.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e803d9470f31"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_name", sa.String(length=50), nullable=False),
        # "fine_projection" | "fine_mitigation" | "cmir" | "po_validation"
        sa.Column("source", sa.String(length=30), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(op.f("ix_agent_agent_name"), "agent", ["agent_name"], unique=True, schema="fines")
    op.create_table(
        "carrier",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("carrier_id", sa.String(length=50), nullable=False),
        sa.Column("carrier_name", sa.String(length=100), nullable=False),
        sa.Column("historical_reliability_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(op.f("ix_carrier_carrier_id"), "carrier", ["carrier_id"], unique=True, schema="fines")
    op.create_table(
        "job_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_type", sa.String(length=30), nullable=False),
        sa.Column("projection_date", sa.Date(), nullable=False),
        sa.Column("stacking_mode_override", sa.String(length=30), nullable=True),
        sa.Column("triggered_by", sa.String(length=100), nullable=True),
        sa.Column("requested_item_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "run_type IN ('MANUAL_BATCH', 'ON_DEMAND', 'SCHEDULED_DAILY')", name="ck_job_run_run_type"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_table(
        "location",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.String(length=50), nullable=False),
        sa.Column("location_name", sa.String(length=100), nullable=True),
        sa.Column("location_type", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(op.f("ix_location_location_id"), "location", ["location_id"], unique=True, schema="fines")
    op.create_table(
        "retailer",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("retailer_id", sa.String(length=50), nullable=False),
        sa.Column("retailer_name", sa.String(length=100), nullable=False),
        sa.Column("priority_tier", sa.String(length=30), nullable=True),
        sa.Column("stacking_mode", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(op.f("ix_retailer_retailer_id"), "retailer", ["retailer_id"], unique=True, schema="fines")
    op.create_table(
        "sku",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sku_id", sa.String(length=50), nullable=False),
        sa.Column("sku_code", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(op.f("ix_sku_sku_id"), "sku", ["sku_id"], unique=True, schema="fines")
    op.create_table(
        "fine_rule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.String(length=50), nullable=False),
        sa.Column("retailer_id", sa.String(length=50), nullable=False),
        sa.Column("violation_type", sa.String(length=30), nullable=False),
        sa.Column("threshold_pct", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("calc_type", sa.String(length=30), nullable=False),
        sa.Column("rate", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("cap_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("grace_period_days", sa.Integer(), nullable=False),
        sa.Column("effective_start_date", sa.Date(), nullable=False),
        sa.Column("effective_end_date", sa.Date(), nullable=True),
        sa.Column("source_doc_reference", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["retailer_id"],
            ["fines.retailer.retailer_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(op.f("ix_fine_rule_rule_id"), "fine_rule", ["rule_id"], unique=True, schema="fines")
    op.create_table(
        "production_schedule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("production_id", sa.String(length=50), nullable=False),
        sa.Column("sku_id", sa.String(length=50), nullable=False),
        sa.Column("location_id", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("status_date", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["fines.location.location_id"],
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["fines.sku.sku_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        op.f("ix_production_schedule_production_id"),
        "production_schedule",
        ["production_id"],
        unique=True,
        schema="fines",
    )
    op.create_table(
        "prompt_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("module_path", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["fines.agent.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "prompt_version", name="uq_prompt_version_agent_version"),
        schema="fines",
    )
    op.create_table(
        "sales_order",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("retailer_id", sa.String(length=50), nullable=False),
        sa.Column("sku_id", sa.String(length=50), nullable=False),
        sa.Column("ship_from_location_id", sa.String(length=50), nullable=False),
        sa.Column("order_qty", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("requested_delivery_date", sa.Date(), nullable=False),
        sa.Column("required_ship_date", sa.Date(), nullable=False),
        sa.Column("order_status", sa.String(length=30), nullable=False),
        sa.Column("carrier_id", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["carrier_id"],
            ["fines.carrier.carrier_id"],
        ),
        sa.ForeignKeyConstraint(
            ["retailer_id"],
            ["fines.retailer.retailer_id"],
        ),
        sa.ForeignKeyConstraint(
            ["ship_from_location_id"],
            ["fines.location.location_id"],
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["fines.sku.sku_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(op.f("ix_sales_order_order_id"), "sales_order", ["order_id"], unique=True, schema="fines")
    op.create_table(
        "actual_fine",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actual_fine_id", sa.String(length=50), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("retailer_id", sa.String(length=50), nullable=False),
        sa.Column("violation_type", sa.String(length=30), nullable=False),
        sa.Column("actual_fine_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("invoice_or_deduction_date", sa.Date(), nullable=False),
        sa.Column("dispute_status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.ForeignKeyConstraint(
            ["retailer_id"],
            ["fines.retailer.retailer_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        op.f("ix_actual_fine_actual_fine_id"),
        "actual_fine",
        ["actual_fine_id"],
        unique=True,
        schema="fines",
    )
    op.create_table(
        "demand_exception",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("exception_id", sa.String(length=50), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("flagged_date", sa.Date(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        op.f("ix_demand_exception_exception_id"),
        "demand_exception",
        ["exception_id"],
        unique=True,
        schema="fines",
    )
    op.create_table(
        "fine_rule_tier",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tier_id", sa.String(length=50), nullable=False),
        sa.Column("rule_id", sa.String(length=50), nullable=False),
        sa.Column("band_min", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("band_max", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("rate", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["fines.fine_rule.rule_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        op.f("ix_fine_rule_tier_tier_id"), "fine_rule_tier", ["tier_id"], unique=True, schema="fines"
    )
    op.create_table(
        "job_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_run_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("projection_date", sa.Date(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("stacking_mode_override", sa.String(length=30), nullable=True),
        sa.Column("force_regenerate_summary", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('DEAD', 'PENDING', 'RUNNING', 'SUCCEEDED')", name="ck_job_item_status"
        ),
        sa.CheckConstraint(
            "task_type IN ('MITIGATION_SUMMARY_REGEN', 'ORDER_RUN', 'PROJECTION_SUMMARY_REGEN')",
            name="ck_job_item_task_type",
        ),
        sa.ForeignKeyConstraint(
            ["job_run_id"],
            ["fines.job_run.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        "ix_job_item_claimable", "job_item", ["status", "available_at"], unique=False, schema="fines"
    )
    op.create_index("ix_job_item_job_run_id", "job_item", ["job_run_id"], unique=False, schema="fines")
    # Partial unique index -- deliberately NOT expressible via the ORM model
    # (SQLAlchemy's `postgresql_where=` on an `Index` is silently dropped on
    # SQLite, which would otherwise create a *full* unique index in the
    # SQLite test database and wrongly reject a legitimate second terminal
    # (SUCCEEDED/DEAD) row for the same order/date/task). Raw DDL only; see
    # app/models/job_queue.py::JobItem docstring and docs/DATABASE.md for the
    # full explanation, including why tests/test_migration_parity.py cannot
    # catch a divergence here (it only compares table/column names, not
    # indexes). Branched by dialect (not left Postgres-only) so it also
    # renders on SQLite -- same style as the original ff84c023d5e9.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE UNIQUE INDEX uq_job_item_inflight ON fines.job_item "
            "(order_id, projection_date, task_type) WHERE status IN ('PENDING', 'RUNNING')"
        )
    else:
        op.execute(
            "CREATE UNIQUE INDEX uq_job_item_inflight ON job_item "
            "(order_id, projection_date, task_type) WHERE status IN ('PENDING', 'RUNNING')"
        )
    op.create_table(
        "mitigation_input",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("shortage_cause", sa.String(length=30), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        op.f("ix_mitigation_input_order_id"),
        "mitigation_input",
        ["order_id"],
        unique=True,
        schema="fines",
    )
    op.create_table(
        "mitigation_option",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("projection_date", sa.Date(), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("projected_fine_after", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("action_cost", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("net_saving", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("risk_level", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.String(length=30), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "projection_date", "action", name="uq_mitigation_result_order_date_action"
        ),
        schema="fines",
    )
    op.create_table(
        "mitigation_summary",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("source_as_of_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id", "prompt_version"],
            ["fines.prompt_version.agent_id", "fines.prompt_version.prompt_version"],
            name="fk_fine_mitigation_summary_agent_prompt_version",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "as_of_date", "prompt_version", name="uq_fine_mitigation_summary_order_date_prompt"
        ),
        schema="fines",
    )
    op.create_table(
        "order_confirmation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("confirmation_id", sa.String(length=50), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("confirmed_qty", sa.Integer(), nullable=False),
        sa.Column("confirmation_date", sa.DateTime(), nullable=False),
        sa.Column("cut_reason_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        op.f("ix_order_confirmation_confirmation_id"),
        "order_confirmation",
        ["confirmation_id"],
        unique=True,
        schema="fines",
    )
    op.create_table(
        "projected_fine",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("rule_id", sa.String(length=50), nullable=False),
        sa.Column("projection_date", sa.Date(), nullable=False),
        sa.Column("violation_type", sa.String(length=30), nullable=False),
        sa.Column("failure_probability", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("projected_fine_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("days_to_delivery", sa.Integer(), nullable=False),
        sa.Column("projection_status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["fines.fine_rule.rule_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "rule_id", "projection_date", name="uq_projected_fine_order_rule_date"
        ),
        schema="fines",
    )
    op.create_table(
        "projection_summary",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("source_as_of_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id", "prompt_version"],
            ["fines.prompt_version.agent_id", "fines.prompt_version.prompt_version"],
            name="fk_fine_projection_summary_agent_prompt_version",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "as_of_date", "prompt_version", name="uq_fine_projection_summary_order_date_prompt"
        ),
        schema="fines",
    )
    op.create_table(
        "shipment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.String(length=50), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("carrier_id", sa.String(length=50), nullable=True),
        sa.Column("expected_ship_date", sa.Date(), nullable=True),
        sa.Column("actual_ship_date", sa.Date(), nullable=True),
        sa.Column("appointment_status", sa.String(length=30), nullable=False),
        sa.Column("expected_transit_days", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["carrier_id"],
            ["fines.carrier.carrier_id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(op.f("ix_shipment_shipment_id"), "shipment", ["shipment_id"], unique=True, schema="fines")


def downgrade() -> None:
    op.drop_index(op.f("ix_shipment_shipment_id"), table_name="shipment", schema="fines")
    op.drop_table("shipment", schema="fines")
    op.drop_table("projection_summary", schema="fines")
    op.drop_table("projected_fine", schema="fines")
    op.drop_index(
        op.f("ix_order_confirmation_confirmation_id"), table_name="order_confirmation", schema="fines"
    )
    op.drop_table("order_confirmation", schema="fines")
    op.drop_table("mitigation_summary", schema="fines")
    op.drop_table("mitigation_option", schema="fines")
    op.drop_index(op.f("ix_mitigation_input_order_id"), table_name="mitigation_input", schema="fines")
    op.drop_table("mitigation_input", schema="fines")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX fines.uq_job_item_inflight")
    else:
        op.execute("DROP INDEX uq_job_item_inflight")
    op.drop_index("ix_job_item_job_run_id", table_name="job_item", schema="fines")
    op.drop_index("ix_job_item_claimable", table_name="job_item", schema="fines")
    op.drop_table("job_item", schema="fines")
    op.drop_index(op.f("ix_fine_rule_tier_tier_id"), table_name="fine_rule_tier", schema="fines")
    op.drop_table("fine_rule_tier", schema="fines")
    op.drop_index(op.f("ix_demand_exception_exception_id"), table_name="demand_exception", schema="fines")
    op.drop_table("demand_exception", schema="fines")
    op.drop_index(op.f("ix_actual_fine_actual_fine_id"), table_name="actual_fine", schema="fines")
    op.drop_table("actual_fine", schema="fines")
    op.drop_index(op.f("ix_sales_order_order_id"), table_name="sales_order", schema="fines")
    op.drop_table("sales_order", schema="fines")
    op.drop_table("prompt_version", schema="fines")
    op.drop_index(
        op.f("ix_production_schedule_production_id"), table_name="production_schedule", schema="fines"
    )
    op.drop_table("production_schedule", schema="fines")
    op.drop_index(op.f("ix_fine_rule_rule_id"), table_name="fine_rule", schema="fines")
    op.drop_table("fine_rule", schema="fines")
    op.drop_index(op.f("ix_sku_sku_id"), table_name="sku", schema="fines")
    op.drop_table("sku", schema="fines")
    op.drop_index(op.f("ix_retailer_retailer_id"), table_name="retailer", schema="fines")
    op.drop_table("retailer", schema="fines")
    op.drop_index(op.f("ix_location_location_id"), table_name="location", schema="fines")
    op.drop_table("location", schema="fines")
    op.drop_table("job_run", schema="fines")
    op.drop_index(op.f("ix_carrier_carrier_id"), table_name="carrier", schema="fines")
    op.drop_table("carrier", schema="fines")
    op.drop_index(op.f("ix_agent_agent_name"), table_name="agent", schema="fines")
    op.drop_table("agent", schema="fines")
