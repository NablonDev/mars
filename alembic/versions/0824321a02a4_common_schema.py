"""Common schema.

Revision ID: 0824321a02a4
Revises:
Create Date: 2026-08-29

First of five revisions replacing the old 3-revision `fines`/`cmir`/`public`
chain (`e803d9470f31` -> `43d8ced96170` -> `e04c67e98dda`) with a fresh
squash across four schemas this project owns (`common`, `process`, `cmir`,
`penalties`) plus a fifth, empty `langgraph` schema. See
docs/DATABASE.md and the approved Phase 1 restructure plan for the full
rationale (ERP-normalized ` common` schema, `process` job/agent/workflow
backbone shared by both domains, full `fine`->`penalty` rename).

This revision creates every `common`-schema table: shared master data
(retailer, sku, material/material_master, plant/storage_location/
warehouse, retailer_location, carrier) and fulfillment facts
(purchase_order/purchase_order_line, order_confirmation/*_line,
delivery/*_line/shipment, production_order/production_schedule,
demand_exception). Every FK across all five revisions points at a
surrogate `uuid -> <table>.id`, not a business-key column -- the single
largest mechanical change from the old schema.

Local dev DB: this squash is not reversible against pre-existing data
(table/column renames throughout, not just additive changes) -- drop and
recreate the local dev database against this chain, per the approved
plan. `scripts/ops/repair_pre_squash_db.py` does not cover this jump.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateSchema

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0824321a02a4"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "common"

# Every Postgres schema this project owns, created here (idempotently) so
# `alembic upgrade head --sql` (offline mode, no live connection -- used for
# DBA-gated deploys) actually emits the CREATE SCHEMA statements. The online
# path also has `ensure_project_schemas_exist` in alembic/env.py running
# before any revision, which makes this redundant there -- kept anyway since
# it's cheap (IF NOT EXISTS) and this is the one place both paths agree.
# `langgraph` is deliberately excluded -- its own migration creates it.
_PROJECT_SCHEMAS = ("common", "process", "cmir", "penalties")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for schema_name in _PROJECT_SCHEMAS:
            op.execute(CreateSchema(schema_name, if_not_exists=True))

    op.create_table(
        "material",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_material_material_code"), "material", ["material_code"], unique=True, schema=SCHEMA
    )

    op.create_table(
        "plant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plant_code", sa.String(length=50), nullable=False),
        sa.Column("plant_name", sa.String(length=200), nullable=True),
        sa.Column("country_code", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_plant_plant_code"), "plant", ["plant_code"], unique=True, schema=SCHEMA)

    op.create_table(
        "retailer",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("retailer_code", sa.String(length=50), nullable=False),
        sa.Column("retailer_name", sa.String(length=200), nullable=False),
        sa.Column("priority_tier", sa.String(length=30), nullable=True),
        sa.Column("stacking_mode", sa.String(length=30), nullable=False),
        sa.Column("source_system", sa.String(length=50), nullable=True),
        sa.Column("extension_min_lead_days", sa.Integer(), nullable=False),
        sa.Column("extension_response_sla_hours", sa.Integer(), nullable=False),
        sa.Column("extension_penalty_threshold", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_retailer_retailer_code"), "retailer", ["retailer_code"], unique=True, schema=SCHEMA
    )

    op.create_table(
        "carrier",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("carrier_code", sa.String(length=50), nullable=False),
        sa.Column("carrier_name", sa.String(length=200), nullable=False),
        sa.Column("historical_reliability_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_carrier_carrier_code"), "carrier", ["carrier_code"], unique=True, schema=SCHEMA)

    op.create_table(
        "sku",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sku_code", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], [f"{SCHEMA}.material.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_sku_sku_code"), "sku", ["sku_code"], unique=True, schema=SCHEMA)

    op.create_table(
        "storage_location",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plant_id", sa.Uuid(), nullable=False),
        sa.Column("storage_location_code", sa.String(length=50), nullable=False),
        sa.Column("storage_location_name", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["plant_id"], [f"{SCHEMA}.plant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plant_id", "storage_location_code", name="uq_storage_location_plant_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "warehouse",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_code", sa.String(length=50), nullable=False),
        sa.Column("warehouse_name", sa.String(length=200), nullable=True),
        sa.Column("plant_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["plant_id"], [f"{SCHEMA}.plant.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_warehouse_warehouse_code"), "warehouse", ["warehouse_code"], unique=True, schema=SCHEMA
    )

    op.create_table(
        "retailer_location",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("retailer_id", sa.Uuid(), nullable=False),
        sa.Column("location_code", sa.String(length=100), nullable=False),
        sa.Column("location_name", sa.String(length=200), nullable=True),
        sa.Column("location_type", sa.String(length=50), nullable=True),
        sa.Column("address_line_1", sa.String(length=255), nullable=True),
        sa.Column("address_line_2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state_province", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=30), nullable=True),
        sa.Column("country_code", sa.String(length=10), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["retailer_id"], [f"{SCHEMA}.retailer.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("retailer_id", "location_code", name="uq_retailer_location_retailer_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "material_master",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("sap_material_number", sa.String(length=64), nullable=False),
        sa.Column("plant_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("available_quantity", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("uom", sa.String(length=30), nullable=True),
        sa.Column("discontinuation_indicator", sa.String(length=30), nullable=True),
        sa.Column("effective_out_date", sa.Date(), nullable=True),
        sa.Column("follow_up_material_id", sa.Uuid(), nullable=True),
        sa.Column("source_system", sa.String(length=50), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["follow_up_material_id"], [f"{SCHEMA}.material.id"]),
        sa.ForeignKeyConstraint(["material_id"], [f"{SCHEMA}.material.id"]),
        sa.ForeignKeyConstraint(["plant_id"], [f"{SCHEMA}.plant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("material_id", "plant_id", name="uq_material_master_material_plant"),
        schema=SCHEMA,
    )

    op.create_table(
        "purchase_order",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_number", sa.String(length=50), nullable=False),
        sa.Column("retailer_id", sa.Uuid(), nullable=False),
        sa.Column("retailer_po_number", sa.String(length=100), nullable=True),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("required_ship_date", sa.Date(), nullable=True),
        sa.Column("order_status", sa.String(length=50), nullable=False),
        sa.Column("source_system", sa.String(length=50), nullable=True),
        sa.Column("source_document_type", sa.String(length=50), nullable=True),
        sa.Column("source_document_number", sa.String(length=100), nullable=True),
        sa.Column("current_delivery_date", sa.Date(), nullable=True),
        sa.Column("current_required_ship_date", sa.Date(), nullable=True),
        sa.Column("negotiation_status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["retailer_id"], [f"{SCHEMA}.retailer.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_purchase_order_purchase_order_number"),
        "purchase_order",
        ["purchase_order_number"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_purchase_order_negotiation_status"),
        "purchase_order",
        ["negotiation_status"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "purchase_order_line",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.String(length=50), nullable=False),
        sa.Column("retailer_po_line_number", sa.String(length=50), nullable=True),
        sa.Column("sku_id", sa.Uuid(), nullable=True),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("retailer_material_code", sa.String(length=100), nullable=True),
        sa.Column("plant_id", sa.Uuid(), nullable=True),
        sa.Column("storage_location_id", sa.Uuid(), nullable=True),
        sa.Column("ship_to_location_id", sa.Uuid(), nullable=True),
        sa.Column("ordered_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("uom", sa.String(length=30), nullable=True),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("required_ship_date", sa.Date(), nullable=True),
        sa.Column("line_status", sa.String(length=50), nullable=False),
        sa.Column(
            "raw_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], [f"{SCHEMA}.material.id"]),
        sa.ForeignKeyConstraint(["plant_id"], [f"{SCHEMA}.plant.id"]),
        sa.ForeignKeyConstraint(["purchase_order_id"], [f"{SCHEMA}.purchase_order.id"]),
        sa.ForeignKeyConstraint(["ship_to_location_id"], [f"{SCHEMA}.retailer_location.id"]),
        sa.ForeignKeyConstraint(["sku_id"], [f"{SCHEMA}.sku.id"]),
        sa.ForeignKeyConstraint(["storage_location_id"], [f"{SCHEMA}.storage_location.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("purchase_order_id", "line_number", name="uq_purchase_order_line_po_line_number"),
        schema=SCHEMA,
    )

    op.create_table(
        "order_confirmation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("confirmation_number", sa.String(length=100), nullable=False),
        sa.Column("purchase_order_id", sa.Uuid(), nullable=False),
        sa.Column("confirmation_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["purchase_order_id"], [f"{SCHEMA}.purchase_order.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_order_confirmation_confirmation_number"),
        "order_confirmation",
        ["confirmation_number"],
        unique=True,
        schema=SCHEMA,
    )

    op.create_table(
        "order_confirmation_line",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_confirmation_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_line_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("confirmed_delivery_date", sa.Date(), nullable=True),
        sa.Column("cut_reason_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["order_confirmation_id"], [f"{SCHEMA}.order_confirmation.id"]),
        sa.ForeignKeyConstraint(["purchase_order_line_id"], [f"{SCHEMA}.purchase_order_line.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_confirmation_id", "purchase_order_line_id", name="uq_order_confirmation_line_line"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "delivery",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_number", sa.String(length=50), nullable=False),
        sa.Column("purchase_order_id", sa.Uuid(), nullable=False),
        sa.Column("ship_from_plant_id", sa.Uuid(), nullable=True),
        sa.Column("ship_from_warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("ship_to_location_id", sa.Uuid(), nullable=True),
        sa.Column("delivery_status", sa.String(length=50), nullable=False),
        sa.Column("planned_delivery_date", sa.Date(), nullable=True),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        sa.Column("planned_ship_date", sa.Date(), nullable=True),
        sa.Column("actual_ship_date", sa.Date(), nullable=True),
        sa.Column("goods_issue_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["purchase_order_id"], [f"{SCHEMA}.purchase_order.id"]),
        sa.ForeignKeyConstraint(["ship_from_plant_id"], [f"{SCHEMA}.plant.id"]),
        sa.ForeignKeyConstraint(["ship_from_warehouse_id"], [f"{SCHEMA}.warehouse.id"]),
        sa.ForeignKeyConstraint(["ship_to_location_id"], [f"{SCHEMA}.retailer_location.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_delivery_delivery_number"), "delivery", ["delivery_number"], unique=True, schema=SCHEMA
    )

    op.create_table(
        "delivery_line",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_line_id", sa.Uuid(), nullable=False),
        sa.Column("delivered_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("uom", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["delivery_id"], [f"{SCHEMA}.delivery.id"]),
        sa.ForeignKeyConstraint(["purchase_order_line_id"], [f"{SCHEMA}.purchase_order_line.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_id", "purchase_order_line_id", name="uq_delivery_line_delivery_po_line"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "shipment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("shipment_number", sa.String(length=100), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("carrier_id", sa.Uuid(), nullable=True),
        sa.Column("expected_ship_date", sa.Date(), nullable=True),
        sa.Column("actual_ship_date", sa.Date(), nullable=True),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        sa.Column("expected_transit_days", sa.Integer(), nullable=True),
        sa.Column("appointment_status", sa.String(length=50), nullable=True),
        sa.Column("shipment_status", sa.String(length=50), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["carrier_id"], [f"{SCHEMA}.carrier.id"]),
        sa.ForeignKeyConstraint(["delivery_id"], [f"{SCHEMA}.delivery.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_shipment_shipment_number"), "shipment", ["shipment_number"], unique=True, schema=SCHEMA
    )

    op.create_table(
        "production_order",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("production_order_number", sa.String(length=100), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("plant_id", sa.Uuid(), nullable=True),
        sa.Column("planned_quantity", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("produced_quantity", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("planned_start_date", sa.Date(), nullable=True),
        sa.Column("actual_start_date", sa.Date(), nullable=True),
        sa.Column("planned_end_date", sa.Date(), nullable=True),
        sa.Column("actual_end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], [f"{SCHEMA}.material.id"]),
        sa.ForeignKeyConstraint(["plant_id"], [f"{SCHEMA}.plant.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_production_order_production_order_number"),
        "production_order",
        ["production_order_number"],
        unique=True,
        schema=SCHEMA,
    )

    op.create_table(
        "production_schedule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("production_order_id", sa.Uuid(), nullable=True),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("plant_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_quantity", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], [f"{SCHEMA}.material.id"]),
        sa.ForeignKeyConstraint(["plant_id"], [f"{SCHEMA}.plant.id"]),
        sa.ForeignKeyConstraint(["production_order_id"], [f"{SCHEMA}.production_order.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )

    op.create_table(
        "demand_exception",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("exception_id", sa.String(length=50), nullable=False),
        sa.Column("purchase_order_line_id", sa.Uuid(), nullable=False),
        sa.Column("flagged_date", sa.Date(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["purchase_order_line_id"], [f"{SCHEMA}.purchase_order_line.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_demand_exception_exception_id"),
        "demand_exception",
        ["exception_id"],
        unique=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("demand_exception", schema=SCHEMA)
    op.drop_table("production_schedule", schema=SCHEMA)
    op.drop_table("production_order", schema=SCHEMA)
    op.drop_table("shipment", schema=SCHEMA)
    op.drop_table("delivery_line", schema=SCHEMA)
    op.drop_table("delivery", schema=SCHEMA)
    op.drop_table("order_confirmation_line", schema=SCHEMA)
    op.drop_table("order_confirmation", schema=SCHEMA)
    op.drop_table("purchase_order_line", schema=SCHEMA)
    op.drop_table("purchase_order", schema=SCHEMA)
    op.drop_table("material_master", schema=SCHEMA)
    op.drop_table("retailer_location", schema=SCHEMA)
    op.drop_table("warehouse", schema=SCHEMA)
    op.drop_table("storage_location", schema=SCHEMA)
    op.drop_table("sku", schema=SCHEMA)
    op.drop_table("carrier", schema=SCHEMA)
    op.drop_table("retailer", schema=SCHEMA)
    op.drop_table("plant", schema=SCHEMA)
    op.drop_table("material", schema=SCHEMA)
