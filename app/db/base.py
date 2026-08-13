"""SQLAlchemy declarative base, metadata, and shared database schema configuration."""

import secrets
import time
from uuid import UUID

from sqlalchemy import MetaData, Uuid
from sqlalchemy.orm import DeclarativeBase

CMIR_SCHEMA = "cmir"
FINES_SCHEMA = "fines"

# Keep index names aligned with the names used by Alembic migrations.
INDEX_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=INDEX_NAMING_CONVENTION)


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
