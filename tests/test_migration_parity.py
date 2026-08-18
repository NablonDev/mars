"""
Proves the hand-authored Alembic migrations (alembic/versions/5589e602eefa_*.py
for the fines schema, alembic/versions/3c6d4f03fe8e_*.py for the cmir schema)
actually match app/models/, rather than just asserting it in a
docstring. Builds one SQLite DB via `alembic upgrade head` (walks the whole
chain) and another via `Base.metadata.create_all()`, then diffs table and
column names.

No live Postgres needed -- this only checks structural parity between
the migrations and the ORM, not Postgres-specific DDL correctness. Both
engines go through `apply_sqlite_schema_translation` because
`Base.metadata` has tables bound to the `fines` and `cmir` schemas
(app/db/base.py), which SQLite cannot express -- the same translation
app/db/session.py and alembic/env.py apply, so the tables land unqualified
on both sides and stay comparable.
"""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.db.session import apply_sqlite_schema_translation
from app.models import Base

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tables_and_columns(engine) -> dict[str, set[str]]:
    inspector = inspect(engine)
    return {
        table: {col["name"] for col in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table != "alembic_version"  # Alembic's own bookkeeping table, not part of the domain schema
    }


def test_alembic_migration_matches_orm_models(tmp_path: Path):
    migrated_db = tmp_path / "migrated.db"
    alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{migrated_db}")
    command.upgrade(alembic_cfg, "head")

    migrated_engine = create_engine(f"sqlite:///{migrated_db}")
    migrated_schema = _tables_and_columns(migrated_engine)

    orm_engine = apply_sqlite_schema_translation(create_engine("sqlite://"))
    Base.metadata.create_all(orm_engine)
    orm_schema = _tables_and_columns(orm_engine)

    assert migrated_schema.keys() == orm_schema.keys(), (
        f"Table sets differ.\nMigration only: {migrated_schema.keys() - orm_schema.keys()}\n"
        f"ORM only: {orm_schema.keys() - migrated_schema.keys()}"
    )
    for table in orm_schema:
        assert migrated_schema[table] == orm_schema[table], (
            f"Column mismatch in {table!r}.\n"
            f"Migration only: {migrated_schema[table] - orm_schema[table]}\n"
            f"ORM only: {orm_schema[table] - migrated_schema[table]}"
        )


def test_migration_downgrade_reverses_cleanly(tmp_path: Path):
    db_path = tmp_path / "downgrade.db"
    alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")

    engine = create_engine(f"sqlite:///{db_path}")
    remaining = {t for t in inspect(engine).get_table_names() if t != "alembic_version"}
    assert remaining == set()
