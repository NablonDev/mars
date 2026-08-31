"""SQLAlchemy declarative base, metadata, and shared database schema configuration."""

import secrets
import time
from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, MetaData, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

COMMON_SCHEMA = "common"
PROCESS_SCHEMA = "process"
CMIR_SCHEMA = "cmir"
PENALTIES_SCHEMA = "penalties"
LANGGRAPH_SCHEMA = "langgraph"

# Keep index names aligned with the names used by Alembic migrations.
INDEX_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=INDEX_NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


def generate_uuid7() -> UUID:
    """Generate a time-ordered UUIDv7 for surrogate primary keys."""
    timestamp_ms = time.time_ns() // 1_000_000
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)


UUID_PK = Uuid(as_uuid=True)

# JSONB on Postgres (containment/indexing), plain JSON everywhere else
# SQLite (the whole test suite) has no JSONB compiler at all.
JSONB_OR_JSON = JSON().with_variant(JSONB(), "postgresql")
