"""
SQLAlchemy Declarative Base -- imported by every module under app/models/.

All of this project's tables live in a dedicated `fines` Postgres schema,
declared once here as `MetaData(schema=FINES_SCHEMA)` rather than per model
via `__table_args__ = {"schema": ...}`. That is a correctness requirement,
not a style choice: SQLAlchemy resolves an unqualified `ForeignKey("dim_x.y")`
string against the *owning table's* `metadata.schema`, so schema-bound
metadata leaves every existing bare FK string in app/models/ working
untouched. Setting the schema per model does not get that fallback -- the
FK strings would resolve against the default schema and every one of them
would break. `public` stays free for the shared cross-domain tables owned
by the sibling cmir project (see docs/DATABASE.md).

SQLite (tests) has no schema concept; app/db/session.py translates
FINES_SCHEMA away at the connection level instead of every caller knowing
about it.

`INDEX_NAMING_CONVENTION` is load-bearing, not cosmetic: SQLAlchemy's
default name for a column-level `index=True` is `ix_%(column_0_label)s`,
and `column_0_label` picks up the schema once the metadata is schema-bound
-- so simply setting `schema=FINES_SCHEMA` silently renames every one of
those indexes (`ix_fact_order_order_id` -> `ix_fines_fact_order_order_id`)
and puts the ORM permanently out of step with the names migrations 0001
and 0003 actually created. Pinning the convention to the bare table name
keeps the database and the ORM agreeing on one set of names.

Every table gets a surrogate `id` (UUIDv7) as its actual primary key --
the standard convention, and the one the sibling cmir_agent project
already uses (its `generate_uuid7()` in infrastructure/orm_models.py;
duplicated here rather than importing across projects, since cmir_agent
is a separate, gitignored sibling, not a shared package). Business
identifiers (order_id, retailer_id, rule_id, etc.) stay as regular
`unique=True` columns -- they're what the API, tests, and every worked
example already address orders/rules/retailers by, and there's no reason
to disturb that; the surrogate `id` exists for the primary-key
convention, not to replace how this project's resources are looked up.
"""

import secrets
import time
from uuid import UUID

from sqlalchemy import MetaData, Uuid
from sqlalchemy.orm import DeclarativeBase

FINES_SCHEMA = "fines"

# Only `ix` is overridden -- every other constraint type keeps SQLAlchemy's
# (and therefore Postgres's) existing behaviour, so this changes no name
# that is already in the database.
INDEX_NAMING_CONVENTION = {"ix": "ix_%(table_name)s_%(column_0_name)s"}


class Base(DeclarativeBase):
    metadata = MetaData(schema=FINES_SCHEMA, naming_convention=INDEX_NAMING_CONVENTION)


def generate_uuid7() -> UUID:
    """Time-ordered UUID (v7) so surrogate ids sort roughly by creation
    time -- friendlier for index locality than a fully random UUID v4."""
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
