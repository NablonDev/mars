"""Add PO delivery-change requests.

Revision ID: e04c67e98dda
Revises: 43d8ced96170
Create Date: 2026-08-25

Adds `sales_order.current_delivery_date` / `current_required_ship_date`
(nullable, backfilled below to match `requested_delivery_date` /
`required_ship_date` for every existing order -- see
OrderRepository.build_snapshot's COALESCE for how the projection engine
reads them) and `sales_order.negotiation_status` (denormalized current-state
string, single-writer -- see PoDeliveryChangeRequestService), plus the new
`retailer.extension_min_lead_days` / `extension_response_sla_hours` /
`extension_fine_threshold` per-retailer policy columns, and the new
`po_delivery_change_request` table that records the request/response
lifecycle for asking a retailer to move a committed delivery date. See
app/services/fine_projection/po_delivery_change.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e04c67e98dda"
down_revision: str | None = "43d8ced96170"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# op.add_column's `schema` kwarg doesn't respect the SQLite
# schema_translate_map that apply_sqlite_schema_translation sets up (unlike
# op.create_table/op.create_index, which do) -- it emits a literal
# `ALTER TABLE fines.<table> ...` that SQLite can't resolve. Dialect-branch
# every add_column in this migration the same way, so
# tests/unit/db/test_migration_parity.py's SQLite run also exercises them.
def _schema_kwarg() -> str | None:
    return "fines" if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    add_column_schema = _schema_kwarg()
    op.add_column(
        "sales_order",
        sa.Column("current_delivery_date", sa.Date(), nullable=True),
        schema=add_column_schema,
    )
    op.add_column(
        "sales_order",
        sa.Column("current_required_ship_date", sa.Date(), nullable=True),
        schema=add_column_schema,
    )
    op.add_column(
        "sales_order",
        sa.Column("negotiation_status", sa.String(length=30), server_default="NONE", nullable=False),
        schema=add_column_schema,
    )
    op.create_index(
        op.f("ix_sales_order_negotiation_status"),
        "sales_order",
        ["negotiation_status"],
        unique=False,
        schema=add_column_schema,
    )

    op.add_column(
        "retailer",
        sa.Column("extension_min_lead_days", sa.Integer(), server_default="2", nullable=False),
        schema=add_column_schema,
    )
    op.add_column(
        "retailer",
        sa.Column("extension_response_sla_hours", sa.Integer(), server_default="48", nullable=False),
        schema=add_column_schema,
    )
    op.add_column(
        "retailer",
        sa.Column(
            "extension_fine_threshold",
            sa.Numeric(10, 2),
            server_default="0.0",
            nullable=False,
        ),
        schema=add_column_schema,
    )

    # Backfill: the effective date starts out equal to the original
    # commitment for every pre-existing order. Dialect-branched (not
    # Postgres-only) so tests/unit/db/test_migration_parity.py's SQLite
    # `alembic upgrade head` run also exercises it -- same style as the
    # partial-index branch further up this migration's history
    # (e803d9470f31's uq_job_item_inflight).
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE fines.sales_order SET "
            "current_delivery_date = requested_delivery_date, "
            "current_required_ship_date = required_ship_date"
        )
    else:
        op.execute(
            "UPDATE sales_order SET "
            "current_delivery_date = requested_delivery_date, "
            "current_required_ship_date = required_ship_date"
        )

    op.create_table(
        "po_delivery_change_request",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=50), nullable=False),
        sa.Column("order_id", sa.String(length=50), nullable=False),
        sa.Column("reason_code", sa.String(length=30), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("baseline_delivery_date", sa.Date(), nullable=False),
        sa.Column("proposed_delivery_date", sa.Date(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("retailer_response_date", sa.Date(), nullable=True),
        sa.Column("countered_delivery_date", sa.Date(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column(
            "response_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'COUNTERED', 'REJECTED', 'EXPIRED')",
            name="ck_po_delivery_change_request_status",
        ),
        sa.CheckConstraint(
            "reason_code IN ('SHORTAGE', 'DELAY', 'OTHER')",
            name="ck_po_delivery_change_request_reason_code",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["fines.sales_order.order_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="fines",
    )
    op.create_index(
        op.f("ix_po_delivery_change_request_request_id"),
        "po_delivery_change_request",
        ["request_id"],
        unique=True,
        schema="fines",
    )
    op.create_index(
        "ix_po_delivery_change_request_order_status",
        "po_delivery_change_request",
        ["order_id", "status"],
        unique=False,
        schema="fines",
    )
    op.create_index(
        "ix_po_delivery_change_request_status_expires",
        "po_delivery_change_request",
        ["status", "expires_at"],
        unique=False,
        schema="fines",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_po_delivery_change_request_status_expires",
        table_name="po_delivery_change_request",
        schema="fines",
    )
    op.drop_index(
        "ix_po_delivery_change_request_order_status",
        table_name="po_delivery_change_request",
        schema="fines",
    )
    op.drop_index(
        op.f("ix_po_delivery_change_request_request_id"),
        table_name="po_delivery_change_request",
        schema="fines",
    )
    op.drop_table("po_delivery_change_request", schema="fines")

    drop_column_schema = _schema_kwarg()
    op.drop_column("retailer", "extension_fine_threshold", schema=drop_column_schema)
    op.drop_column("retailer", "extension_response_sla_hours", schema=drop_column_schema)
    op.drop_column("retailer", "extension_min_lead_days", schema=drop_column_schema)

    op.drop_index(
        op.f("ix_sales_order_negotiation_status"),
        table_name="sales_order",
        schema=drop_column_schema,
    )
    op.drop_column("sales_order", "negotiation_status", schema=drop_column_schema)
    op.drop_column("sales_order", "current_required_ship_date", schema=drop_column_schema)
    op.drop_column("sales_order", "current_delivery_date", schema=drop_column_schema)
