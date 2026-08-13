"""
Alembic environment.

Database URL comes from app.core.config.Settings, so DATABASE_URL / .env
remains the single source of truth.

ORM metadata comes directly from app.models. Alembic autogenerate compares
the actual database schema against Base.metadata; there is no separate
hand-maintained schema definition.

Schema layout
-------------
This project intentionally uses two PostgreSQL schemas:

    public
        Models without an explicit schema in __table_args__ live here.

    fines
        Fines-specific models explicitly use:
            __table_args__ = {
                "schema": FINES_SCHEMA
            }

Alembic's own version table lives in `fines` so that this project's migration
history does not conflict with another project's history in `public`.

Autogenerate is restricted to the schemas owned by this project:
    - public
    - fines

Other schemas, such as `cmir`, are deliberately excluded.

SQLite
------
SQLite does not support PostgreSQL schemas. The same schema translation used
by app/db/session.py is applied to the Alembic connection so that tests can
run against SQLite while ORM models continue to use their normal schema
configuration.

Run from the repository root: `alembic upgrade head`
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import Connection, engine_from_config, make_url, pool
from sqlalchemy.schema import CreateSchema

from alembic import context

# Make app.* importable regardless of which directory Alembic was invoked from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.config import get_settings
from app.db.base import FINES_SCHEMA
from app.db.session import apply_sqlite_schema_translation
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Restrict Alembic autogenerate to schemas owned by this project."""
    if type_ == "schema":
        return name is None or name == FINES_SCHEMA
    return True


def _version_table_schema(dialect_name: str) -> str | None:
    """Return the schema in which Alembic should store its version table."""
    return None if dialect_name == "sqlite" else FINES_SCHEMA


def ensure_version_table_schema(connection: Connection) -> None:
    """Ensure the Alembic version-table schema exists."""
    schema = _version_table_schema(connection.dialect.name)
    if schema is None:
        return
    connection.execute(CreateSchema(schema, if_not_exists=True))
    connection.commit()


def run_migrations_offline() -> None:
    """Run migrations without creating a live database connection."""
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "sqlalchemy.url is not set; export DATABASE_URL before running offline"
        )
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=_version_table_schema(make_url(url).get_backend_name()),
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = apply_sqlite_schema_translation(
        engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    )
    with connectable.connect() as connection:
        ensure_version_table_schema(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=_version_table_schema(connection.dialect.name),
            include_schemas=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
