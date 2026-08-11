"""initial schema

Hand-authored to mirror app/models/*.py exactly (no live Postgres in
the dev sandbox this was written in to run --autogenerate against).
tests/test_migration_parity.py proves the two stay in sync by building
one SQLite DB via this migration and another via
Base.metadata.create_all(), then diffing table/column sets -- a real
check, not just a comment promising they match.

Every table has a surrogate `id` (UUID) primary key; business identifiers
(order_id, retailer_id, rule_id, ...) are separate unique columns that
foreign keys reference directly -- see docs/DATABASE.md "Primary keys".

Revision ID: 0001
Revises:
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_PK = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "dim_retailer",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("retailer_id", sa.String(20), nullable=False, unique=True),
        sa.Column("retailer_name", sa.String(100), nullable=False),
        sa.Column("priority_tier", sa.String(20), nullable=True),
        sa.Column("stacking_mode", sa.String(10), nullable=False, server_default="SUM"),
    )
    op.create_index("ix_dim_retailer_retailer_id", "dim_retailer", ["retailer_id"])

    op.create_table(
        "dim_sku",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("sku_id", sa.String(20), nullable=False, unique=True),
        sa.Column("sku_code", sa.String(30), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
    )
    op.create_index("ix_dim_sku_sku_id", "dim_sku", ["sku_id"])

    op.create_table(
        "dim_location",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("location_id", sa.String(20), nullable=False, unique=True),
        sa.Column("location_name", sa.String(100), nullable=True),
        sa.Column("location_type", sa.String(10), nullable=True),
    )
    op.create_index("ix_dim_location_location_id", "dim_location", ["location_id"])

    op.create_table(
        "dim_carrier",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("carrier_id", sa.String(20), nullable=False, unique=True),
        sa.Column("carrier_name", sa.String(100), nullable=False),
        sa.Column("historical_reliability_score", sa.Numeric(5, 2), nullable=False, server_default="90.0"),
    )
    op.create_index("ix_dim_carrier_carrier_id", "dim_carrier", ["carrier_id"])

    op.create_table(
        "dim_penalty_rule",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("rule_id", sa.String(20), nullable=False, unique=True),
        sa.Column("retailer_id", sa.String(20), sa.ForeignKey("dim_retailer.retailer_id"), nullable=False),
        sa.Column("violation_type", sa.String(30), nullable=False),
        sa.Column("threshold_pct", sa.Numeric(6, 4), nullable=False, server_default="0.0"),
        sa.Column("calc_type", sa.String(20), nullable=False),
        sa.Column("rate", sa.Numeric(10, 4), nullable=False, server_default="0.0"),
        sa.Column("cap_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("grace_period_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("effective_start_date", sa.Date, nullable=False),
        sa.Column("effective_end_date", sa.Date, nullable=True),
        sa.Column("source_doc_reference", sa.String(200), nullable=True),
    )
    op.create_index("ix_dim_penalty_rule_rule_id", "dim_penalty_rule", ["rule_id"])

    op.create_table(
        "dim_penalty_rule_tier",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("tier_id", sa.String(40), nullable=False, unique=True),
        sa.Column("rule_id", sa.String(20), sa.ForeignKey("dim_penalty_rule.rule_id"), nullable=False),
        sa.Column("band_min", sa.Numeric(6, 4), nullable=False),
        sa.Column("band_max", sa.Numeric(6, 4), nullable=False),
        sa.Column("rate", sa.Numeric(10, 4), nullable=False),
    )
    op.create_index("ix_dim_penalty_rule_tier_tier_id", "dim_penalty_rule_tier", ["tier_id"])

    op.create_table(
        "fact_order",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("order_id", sa.String(30), nullable=False, unique=True),
        sa.Column("retailer_id", sa.String(20), sa.ForeignKey("dim_retailer.retailer_id"), nullable=False),
        sa.Column("sku_id", sa.String(20), sa.ForeignKey("dim_sku.sku_id"), nullable=False),
        sa.Column(
            "ship_from_location_id", sa.String(20), sa.ForeignKey("dim_location.location_id"), nullable=False
        ),
        sa.Column("order_qty", sa.Integer, nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("order_date", sa.Date, nullable=False),
        sa.Column("requested_delivery_date", sa.Date, nullable=False),
        sa.Column("required_ship_date", sa.Date, nullable=False),
        sa.Column("order_status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("carrier_id", sa.String(20), sa.ForeignKey("dim_carrier.carrier_id"), nullable=True),
    )
    op.create_index("ix_fact_order_order_id", "fact_order", ["order_id"])

    op.create_table(
        "fact_order_confirmation",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("confirmation_id", sa.String(40), nullable=False, unique=True),
        sa.Column("order_id", sa.String(30), sa.ForeignKey("fact_order.order_id"), nullable=False),
        sa.Column("confirmed_qty", sa.Integer, nullable=False),
        sa.Column("confirmation_date", sa.DateTime, nullable=False),
        sa.Column("cut_reason_code", sa.String(40), nullable=True),
    )
    op.create_index(
        "ix_fact_order_confirmation_confirmation_id", "fact_order_confirmation", ["confirmation_id"]
    )

    op.create_table(
        "fact_production_schedule",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("production_id", sa.String(40), nullable=False, unique=True),
        sa.Column("sku_id", sa.String(20), sa.ForeignKey("dim_sku.sku_id"), nullable=False),
        sa.Column("location_id", sa.String(20), sa.ForeignKey("dim_location.location_id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_date", sa.DateTime, nullable=False),
    )
    op.create_index(
        "ix_fact_production_schedule_production_id", "fact_production_schedule", ["production_id"]
    )

    op.create_table(
        "fact_shipment",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("shipment_id", sa.String(40), nullable=False, unique=True),
        sa.Column("order_id", sa.String(30), sa.ForeignKey("fact_order.order_id"), nullable=False),
        sa.Column("carrier_id", sa.String(20), sa.ForeignKey("dim_carrier.carrier_id"), nullable=True),
        sa.Column("expected_ship_date", sa.Date, nullable=True),
        sa.Column("actual_ship_date", sa.Date, nullable=True),
        sa.Column("appointment_status", sa.String(20), nullable=False, server_default="SCHEDULED"),
        sa.Column("expected_transit_days", sa.Integer, nullable=False, server_default="2"),
        sa.Column("recorded_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_fact_shipment_shipment_id", "fact_shipment", ["shipment_id"])

    op.create_table(
        "fact_demand_exception",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("exception_id", sa.String(40), nullable=False, unique=True),
        sa.Column("order_id", sa.String(30), sa.ForeignKey("fact_order.order_id"), nullable=False),
        sa.Column("flagged_date", sa.Date, nullable=False),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_fact_demand_exception_exception_id", "fact_demand_exception", ["exception_id"])

    op.create_table(
        "fact_projected_fine",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("order_id", sa.String(30), sa.ForeignKey("fact_order.order_id"), nullable=False),
        sa.Column("rule_id", sa.String(20), sa.ForeignKey("dim_penalty_rule.rule_id"), nullable=False),
        sa.Column("projection_date", sa.Date, nullable=False),
        sa.Column("violation_type", sa.String(30), nullable=False),
        sa.Column("failure_probability", sa.Numeric(5, 4), nullable=False),
        sa.Column("projected_fine_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("days_to_delivery", sa.Integer, nullable=False),
        sa.Column("projection_status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.UniqueConstraint(
            "order_id", "rule_id", "projection_date", name="uq_projected_fine_order_rule_date"
        ),
    )

    op.create_table(
        "fact_actual_fine",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("actual_fine_id", sa.String(40), nullable=False, unique=True),
        sa.Column("order_id", sa.String(30), sa.ForeignKey("fact_order.order_id"), nullable=False),
        sa.Column("retailer_id", sa.String(20), sa.ForeignKey("dim_retailer.retailer_id"), nullable=False),
        sa.Column("violation_type", sa.String(30), nullable=False),
        sa.Column("actual_fine_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("invoice_or_deduction_date", sa.Date, nullable=False),
        sa.Column("dispute_status", sa.String(20), nullable=False, server_default="NONE"),
    )
    op.create_index("ix_fact_actual_fine_actual_fine_id", "fact_actual_fine", ["actual_fine_id"])


def downgrade() -> None:
    op.drop_table("fact_actual_fine")
    op.drop_table("fact_projected_fine")
    op.drop_table("fact_demand_exception")
    op.drop_table("fact_shipment")
    op.drop_table("fact_production_schedule")
    op.drop_table("fact_order_confirmation")
    op.drop_table("fact_order")
    op.drop_table("dim_penalty_rule_tier")
    op.drop_table("dim_penalty_rule")
    op.drop_table("dim_carrier")
    op.drop_table("dim_location")
    op.drop_table("dim_sku")
    op.drop_table("dim_retailer")
