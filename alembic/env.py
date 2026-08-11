"""
Alembic environment. sqlalchemy.url comes from app.core.config.Settings
(so DATABASE_URL / .env is the single source of truth, not duplicated
into alembic.ini), and target_metadata comes straight from the ORM
models via app.models -- `alembic revision --autogenerate` diffs
against the real models, it doesn't need a hand-maintained shadow schema.

This project's tables live in the `fines` schema (app/db/base.py), so:

* Alembic's own `alembic_version` bookkeeping table lives in `fines` too,
  not `public` -- otherwise this project and the sibling cmir project
  would fight over one shared `public.alembic_version` with two
  independent migration histories. That table is created before any
  migration runs, so `ensure_version_table_schema` creates the schema
  first; on an *existing* database the move is a **two-phase rollout**
  that needs a manual step -- see docs/RUNBOOK.md §3.
* autogenerate reflects with `include_schemas=True` but `include_name`
  restricts it to `fines`, so it never tries to diff `public` (or a
  future `cmir`) and propose dropping tables this project doesn't own.
* SQLite (tests/test_migration_parity.py) has no schemas, so the same
  `schema_translate_map` used by app/db/session.py is applied to the
  connectable, and `version_table_schema` is left unqualified.

Run from the repo root: `alembic upgrade head`
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import Connection, engine_from_config, make_url, pool
from sqlalchemy.schema import CreateSchema

from alembic import context

# Make `app.*` importable regardless of which directory alembic was
# invoked from.
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

# Respect a URL already set on the Config object (e.g. a test calling
# `command.upgrade(cfg, "head")` after `cfg.set_main_option(...)`) --
# only fall back to Settings/.env when nothing more specific was given.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Keep autogenerate inside the `fines` schema. With
    `include_schemas=True`, Alembic reflects every schema it can see and
    would otherwise propose dropping `public.*` -- tables owned by the
    sibling cmir project, which this project must never migrate. `name is
    None` is the connection's default schema (`public` on Postgres), which
    is exactly what we want excluded.
    """
    if type_ == "schema":
        return name == FINES_SCHEMA
    return True


def _version_table_schema(dialect_name: str) -> str | None:
    """SQLite has no schemas. Alembic checks for the version table through
    the inspector, which does *not* go through `schema_translate_map`, so
    asking for `fines.alembic_version` there would fail regardless of the
    translation applied to the connection -- leave it unqualified.
    """
    return None if dialect_name == "sqlite" else FINES_SCHEMA


def ensure_version_table_schema(connection: Connection) -> None:
    """Create the `fines` schema if it is missing, before Alembic touches
    its version table.

    Alembic creates `alembic_version` *before* running any migration, so on
    an empty database it would look for `fines.alembic_version` while the
    schema that 0004_move_to_fines_schema creates does not exist yet --
    `alembic upgrade head` could not bootstrap a new environment (fresh dev
    machine, CI, staging) at all. `CREATE SCHEMA IF NOT EXISTS` is
    idempotent and costs nothing on an already-migrated database.

    This does *not* remove the need for the two-phase rollout on an
    existing database (docs/RUNBOOK.md §3): there the schema being absent
    was never the real problem -- the version table sitting in `public`
    with the migration history in it is, and only a human can move that.
    """
    schema = _version_table_schema(connection.dialect.name)
    if schema is None:
        return
    connection.execute(CreateSchema(schema, if_not_exists=True))
    connection.commit()


def run_migrations_offline() -> None:
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
