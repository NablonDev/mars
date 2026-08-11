"""add fact_projection_explanation

Mirrors app/models/explanations.py::ProjectionExplanation
column-for-column. See docs/DATABASE.md and
docs/mars_fines_projection_schema.sql for the conceptual write-up.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_PK = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "fact_projection_explanation",
        sa.Column("id", UUID_PK, primary_key=True),
        sa.Column("order_id", sa.String(30), sa.ForeignKey("fact_order.order_id"), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("explanation_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "order_id", "as_of_date", "prompt_version", name="uq_projection_explanation_order_date_prompt"
        ),
    )


def downgrade() -> None:
    op.drop_table("fact_projection_explanation")
