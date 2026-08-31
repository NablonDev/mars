"""
Alembic environment.

Database URL comes from app.core.config.Settings, so DATABASE_URL / .env
remains the single source of truth.

ORM metadata comes directly from app.models. Alembic autogenerate compares
the actual database schema against Base.metadata; there is no separate
hand-maintained schema definition.

Schema layout
-------------
This project uses five PostgreSQL schemas: `common`, `process`, `cmir`,
`penalties`, and `langgraph`. No domain tables live in `public`.

    common
        Shared master/fulfillment data (retailer, sku, material,
        purchase_order, ...), used by both the `cmir`/`po_validation` and
        `penalties` domains.

    process
        The shared job/agent/workflow backbone (job_run, job_item,
        workflow_thread, agent, agent_run, agent_trace, human_action,
        processing_error), consolidating what were two parallel stacks
        (the old `fines` job_run/job_item, and `cmir`'s agent_runs/
        agent_traces/workflow_threads/hitl_actions/pending_human_actions).
        Alembic's own version table lives here (see `_version_table_schema`
        below) -- it tracks one linear migration history covering every
        schema this project owns, so it belongs in a schema neither
        domain exclusively owns, not tucked inside `cmir` or `penalties`
        as if it were one domain's private bookkeeping.

    cmir
        CMIR/PO-validation-only models, via:
            __table_args__ = {"schema": CMIR_SCHEMA}

    penalties
        Penalties-only models (renamed from `fines`), via:
            __table_args__ = {"schema": PENALTIES_SCHEMA}

    langgraph
        Created empty by its own migration for LangGraph's PostgresSaver
        checkpoint tables (checkpoints, checkpoint_blobs, ...), which the
        saver creates and owns at runtime -- never via Alembic, and never
        included in autogenerate (see `include_name` below).

Autogenerate is restricted to the schemas this project's ORM models own:
    - common
    - process
    - cmir
    - penalties
(`langgraph` is deliberately excluded -- see above.)

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
from app.db.base import CMIR_SCHEMA, COMMON_SCHEMA, PENALTIES_SCHEMA, PROCESS_SCHEMA
from app.db.session import apply_sqlite_schema_translation
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database.url)

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Restrict Alembic autogenerate to schemas owned by this project.

    Excludes the version table itself by name: normally Alembic skips its
    own bookkeeping table from comparison automatically, but that implicit
    exclusion is bypassed once a custom include_name is supplied and the
    version table's schema (`common`) is itself an included schema --
    without this, autogenerate proposes dropping `alembic_version` every
    time.
    """
    if type_ == "schema":
        return name is None or name in (COMMON_SCHEMA, PROCESS_SCHEMA, CMIR_SCHEMA, PENALTIES_SCHEMA)
    return not (type_ == "table" and name == "alembic_version")


def _version_table_schema(dialect_name: str) -> str | None:
    """Return the schema in which Alembic should store its version table.

    `common` rather than `process`/`cmir`/`penalties`: the version table
    tracks one history spanning every schema this project owns, so it
    belongs in a schema no single domain owns -- `common` is the closest
    fit (shared master data, not one domain's private bookkeeping).
    """
    return None if dialect_name == "sqlite" else COMMON_SCHEMA


def ensure_project_schemas_exist(connection: Connection) -> None:
    """Ensure every Postgres schema this project owns exists.

    Runs once before migrations so a brand-new database doesn't need a
    hand-run `CREATE SCHEMA` before `alembic upgrade head` -- covers every
    domain schema a migration might create tables in, including `common`
    (where the version table lives, see `_version_table_schema`): unlike
    the old `public`-based version table, `common` does NOT already exist
    on a fresh Postgres database, so it must be created here before
    Alembic's version table can be written. `langgraph` is deliberately
    excluded -- its own migration creates that schema itself.
    """
    if connection.dialect.name == "sqlite":
        return
    for schema in (COMMON_SCHEMA, PROCESS_SCHEMA, CMIR_SCHEMA, PENALTIES_SCHEMA):
        connection.execute(CreateSchema(schema, if_not_exists=True))
    connection.commit()


def run_migrations_offline() -> None:
    """Run migrations without creating a live database connection."""
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not set; export DATABASE_URL before running offline")
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
        ensure_project_schemas_exist(connection)
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
