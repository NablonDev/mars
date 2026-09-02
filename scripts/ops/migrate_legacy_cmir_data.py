#!/usr/bin/env python3
"""
One-off import of the legacy standalone `cmir_db` database (pre fines/cmir
merge, `public` schema, int-keyed `agent_runs`) into this repo's `mars`
database, `cmir` schema (UUIDv7-keyed). See docs/DATABASE.md's "Postgres
schema separation" / "CMIR / PO Validation tables" sections for the target
shape.

`agent_runs`/`agent_trace`/`email_action_log`/`hitl_actions`/
`pending_human_actions`/`cmir_records` all moved from serial int PKs to
UUIDv7 during the merge (`agent_trace` and `email_action_log` also lost
their singular names). This script mints a fresh UUIDv7 per old row via
`app.db.base.generate_uuid7` and remaps every FK that pointed at an old int
id. A handful of columns dropped during the merge (`email_events.metadata`/
`received_at`, `cmir_records.approved_by`, `email_action_log.created_at`,
`workflow_threads.pending_action_id`) are read from the source but not
carried over -- there's no home for them in the current schema.

LangGraph's checkpoint tables (checkpoints/checkpoint_blobs/
checkpoint_writes/checkpoint_migrations) are copied unchanged into
`public` on the target, not `cmir` -- `checkpoint_dsn()` carries no schema
override, so `PostgresSaver.setup()` creates them wherever the connection's
default search_path points (`"$user", public` for the `mars` role). They're
keyed by `thread_id` (text), which didn't change shape, so no remap needed.

Read-only against the source. Refuses to run if any target table already
has rows, unless --force (which truncates cmir.* and public.checkpoint*
first). Everything else happens in one transaction against the target.

Usage:
    python scripts/ops/migrate_legacy_cmir_data.py
    python scripts/ops/migrate_legacy_cmir_data.py --source-dsn postgresql://postgres:postgres@localhost:5432/cmir_db
    python scripts/ops/migrate_legacy_cmir_data.py --dry-run
    python scripts/ops/migrate_legacy_cmir_data.py --force
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import get_settings
from app.db.base import generate_uuid7
from app.db.session import checkpoint_dsn

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_DSN = "postgresql://postgres:postgres@localhost:5432/cmir_db"

# Target tables that live in `cmir`, in FK-safe insert order (per the live
# FK graph -- workflow_threads/pending_human_actions/po_line_errors all FK
# into po_lines, so po_lines has to land before them, not after).
CMIR_TABLES_IN_ORDER = [
    "email_events",
    "po_lines",
    "agent_runs",
    "material_master",
    "cmir_records",
    "email_action_logs",
    "workflow_threads",
    "hitl_actions",
    "pending_human_actions",
    "agent_traces",
    "po_line_errors",
]
# checkpoint_migrations deliberately excluded: it's langgraph's own
# schema-version bookkeeping for these tables, not data, and PostgresSaver
# .setup() below already (re-)populates it for the installed langgraph
# version -- copying the source's rows over that would be meaningless.
CHECKPOINT_TABLES = [
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
]

# The two tables renamed singular -> plural during the merge. Everything
# else keeps the same name on both sides.
SOURCE_TABLE_NAME = {
    "email_action_logs": "email_action_log",
    "agent_traces": "agent_trace",
}


def _fetch_all(conn: psycopg.Connection, table: str) -> list[dict]:
    source_table = SOURCE_TABLE_NAME.get(table, table)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM public.{source_table}")
        return cur.fetchall()


def _insert_many(
    conn: psycopg.Connection, schema: str, table: str, columns: list[str], rows: list[dict]
) -> None:
    if not rows:
        return
    col_list = ", ".join(columns)
    placeholders = ", ".join(f"%({c})s" for c in columns)
    # psycopg3 doesn't auto-serialize dict/list back to json/jsonb on the way
    # in (it only auto-parses on the way out) -- wrap them explicitly.
    prepared = [{k: Json(v) if isinstance(v, (dict, list)) else v for k, v in row.items()} for row in rows]
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {schema}.{table} ({col_list}) VALUES ({placeholders})",
            prepared,
        )


def _fetchone(cur: psycopg.Cursor) -> tuple:
    """cur.fetchone() typed as possibly-None even for a query that always
    returns exactly one row (count(*), to_regclass(...)) -- narrow it once."""
    row = cur.fetchone()
    assert row is not None
    return row


def _target_row_counts(conn: psycopg.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in CMIR_TABLES_IN_ORDER:
            cur.execute(f"SELECT count(*) FROM cmir.{table}")
            counts[f"cmir.{table}"] = _fetchone(cur)[0]
        for table in CHECKPOINT_TABLES:
            cur.execute(f"SELECT to_regclass('public.{table}')")
            if _fetchone(cur)[0] is None:
                counts[f"public.{table}"] = 0
                continue
            cur.execute(f"SELECT count(*) FROM public.{table}")
            counts[f"public.{table}"] = _fetchone(cur)[0]
    return counts


def _truncate_target(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(f"cmir.{t}" for t in CMIR_TABLES_IN_ORDER) + " CASCADE")
        for table in CHECKPOINT_TABLES:
            cur.execute(f"SELECT to_regclass('public.{table}')")
            if _fetchone(cur)[0] is not None:
                cur.execute(f"TRUNCATE public.{table} CASCADE")


def _new_id_map(rows: list[dict]) -> dict[int, UUID]:
    return {row["id"]: generate_uuid7() for row in rows}


def migrate(source_dsn: str, target_dsn: str, *, dry_run: bool, force: bool) -> None:
    with psycopg.connect(source_dsn) as source_conn:
        source = {table: _fetch_all(source_conn, table) for table in CMIR_TABLES_IN_ORDER}
        checkpoints = {table: _fetch_all(source_conn, table) for table in CHECKPOINT_TABLES}

    logger.info("Source row counts: %s", {k: len(v) for k, v in {**source, **checkpoints}.items()})

    with psycopg.connect(target_dsn) as target_conn:
        existing = _target_row_counts(target_conn)
        non_empty = {k: v for k, v in existing.items() if v}
        if non_empty and not force:
            raise SystemExit(
                f"Target already has data in {non_empty} -- pass --force to truncate and re-import, "
                "or migrate into a clean database."
            )

        if dry_run:
            logger.info(
                "--dry-run: would import %s (target currently: %s)",
                {k: len(v) for k, v in source.items()},
                existing,
            )
            return

        if non_empty:
            _truncate_target(target_conn)

        # PostgresSaver.setup() creates the checkpoint tables' exact structure
        # for the installed langgraph version -- safer than hand-rolling DDL.
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(target_dsn) as saver:
            saver.setup()

        agent_run_ids = _new_id_map(source["agent_runs"])
        cmir_record_ids = _new_id_map(source["cmir_records"])
        email_action_log_ids = _new_id_map(source["email_action_logs"])
        hitl_action_ids = _new_id_map(source["hitl_actions"])
        pending_human_action_ids = _new_id_map(source["pending_human_actions"])
        agent_trace_ids = _new_id_map(source["agent_traces"])

        email_events_rows = [
            {
                "id": r["id"],
                "sender": r["sender"],
                "subject": r["subject"],
                "raw_content": r["raw_content"],
                "source_message_id": r["source_message_id"],
                "source_imap_id": r["source_imap_id"],
                "extracted_json": r["extracted_json"],
                "missing_fields": r["missing_fields"],
                "status": r["status"],
                "queue_status": r["queue_status"],
                "queued_at": r["queued_at"],
                "processing_started_at": r["processing_started_at"],
                "processed_at": r["processed_at"],
                "queue_message_id": r["queue_message_id"],
                "queue_delivery_count": r["queue_delivery_count"],
                "queue_error": r["queue_error"],
                "updated_at": r["updated_at"],
                "created_at": r["created_at"],
            }
            for r in source["email_events"]
        ]
        _insert_many(
            target_conn,
            "cmir",
            "email_events",
            list(email_events_rows[0].keys()) if email_events_rows else [],
            email_events_rows,
        )

        po_lines_rows = list(source["po_lines"])
        _insert_many(
            target_conn,
            "cmir",
            "po_lines",
            list(po_lines_rows[0].keys()) if po_lines_rows else [],
            po_lines_rows,
        )

        agent_runs_rows = [
            {
                "id": agent_run_ids[r["id"]],
                "batch_id": r["batch_id"],
                "thread_id": r["thread_id"],
                "email_id": r["email_id"],
                "po_line_id": r["po_line_id"],
                "status": r["status"],
                "current_node": r["current_node"],
                "run_type": r["run_type"],
                "total_threads": r["total_threads"],
                "completed_threads": r["completed_threads"],
                "waiting_threads": r["waiting_threads"],
                "failed_threads": r["failed_threads"],
                "metadata": r["metadata"],
                "started_at": r["started_at"],
                "updated_at": r["updated_at"],
                "completed_at": r["completed_at"],
                "error": r["error"],
            }
            for r in source["agent_runs"]
        ]
        _insert_many(
            target_conn,
            "cmir",
            "agent_runs",
            list(agent_runs_rows[0].keys()) if agent_runs_rows else [],
            agent_runs_rows,
        )

        material_master_rows = list(source["material_master"])
        _insert_many(
            target_conn,
            "cmir",
            "material_master",
            list(material_master_rows[0].keys()) if material_master_rows else [],
            material_master_rows,
        )

        cmir_records_rows = [
            {
                "id": cmir_record_ids[r["id"]],
                "email_id": r["email_id"],
                "sender_type": r["sender_type"],
                "customer_identity": r["customer_identity"],
                "material_identity": r["material_identity"],
                "intent_phrase": r["intent_phrase"],
                "existing_cmir_ref": r["existing_cmir_ref"],
                "brand": r["brand"],
                "site": r["site"],
                "target_grd_code": r["target_grd_code"],
                "target_customer_material_ref": r["target_customer_material_ref"],
                "effective_date": r["effective_date"],
                "reason": r["reason"],
                "customer_identity_key": r["customer_identity_key"],
                "target_customer_material_ref_key": r["target_customer_material_ref_key"],
                "is_current": r["is_current"],
                "valid_from": r["valid_from"],
                "valid_to": r["valid_to"],
                # Left NULL here regardless of the source value -- a superseding
                # row can sort after the row that already points at it, so this
                # self-FK is backfilled in a second pass once every id exists.
                "superseded_by_id": None,
            }
            for r in source["cmir_records"]
        ]
        # 4 source rows had NULL existing_cmir_ref, 5 had NULL target_grd_code --
        # both are NOT NULL in the new schema, so some value here is unavoidable.
        # '' is the correct one, not a stopgap: app.schemas.cmir.CMIR already
        # defaults every content field (these two included) to "", and
        # app.services.cmir_merge already treats a missing value as
        # interchangeable with blank (`str(existing.get(field_name) or "")`) --
        # 3-6 of the *other* rows in this same source table already stored ''
        # rather than NULL for these two columns, so this aligns the stragglers
        # with the representation the app (and most of the legacy data) already
        # used, rather than inventing a new one. Verified exhaustively across
        # every NOT NULL column in every cmir table -- these two are the only
        # ones the source data ever left NULL.
        for r, row in zip(source["cmir_records"], cmir_records_rows, strict=True):
            for col in ("existing_cmir_ref", "target_grd_code"):
                if row[col] is None:
                    row[col] = ""
        _insert_many(
            target_conn,
            "cmir",
            "cmir_records",
            list(cmir_records_rows[0].keys()) if cmir_records_rows else [],
            cmir_records_rows,
        )
        supersessions = [
            (cmir_record_ids[r["superseded_by_id"]], cmir_record_ids[r["id"]])
            for r in source["cmir_records"]
            if r["superseded_by_id"] is not None
        ]
        if supersessions:
            with target_conn.cursor() as cur:
                cur.executemany(
                    "UPDATE cmir.cmir_records SET superseded_by_id = %s WHERE id = %s",
                    supersessions,
                )

        email_action_logs_rows = [
            {
                "id": email_action_log_ids[r["id"]],
                "email_id": r["email_id"],
                "action": r["action"],
                "actor": r["actor"],
                "details": r["details"],
            }
            for r in source["email_action_logs"]
        ]
        _insert_many(
            target_conn,
            "cmir",
            "email_action_logs",
            list(email_action_logs_rows[0].keys()) if email_action_logs_rows else [],
            email_action_logs_rows,
        )

        workflow_threads_rows = [
            {
                "id": r["id"],
                "thread_id": r["thread_id"],
                "batch_id": r["batch_id"],
                "agent_run_id": agent_run_ids[r["agent_run_id"]],
                "email_id": r["email_id"],
                "po_line_id": r["po_line_id"],
                "source_message_id": r["source_message_id"],
                "sender": r["sender"],
                "subject": r["subject"],
                "status": r["status"],
                "current_node": r["current_node"],
                "stage": r["stage"],
                "cmir_status": r["cmir_status"],
                "latest_snapshot": r["latest_snapshot"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "completed_at": r["completed_at"],
                "error": r["error"],
            }
            for r in source["workflow_threads"]
        ]
        _insert_many(
            target_conn,
            "cmir",
            "workflow_threads",
            list(workflow_threads_rows[0].keys()) if workflow_threads_rows else [],
            workflow_threads_rows,
        )

        hitl_actions_rows = [
            {
                "id": hitl_action_ids[r["id"]],
                "run_id": agent_run_ids.get(r["run_id"]) if r["run_id"] else None,
                "batch_id": r["batch_id"],
                "email_id": r["email_id"],
                "interrupt_type": r["interrupt_type"],
                "question": r["question"],
                "answer": r["answer"],
                "decision": r["decision"],
                "reason": r["reason"],
                "actor": r["actor"],
                "responded_at": r["responded_at"],
                "thread_id": r["thread_id"],
                "action_type": r["action_type"],
                "field_changes": r["field_changes"],
                "po_line_id": r["po_line_id"],
            }
            for r in source["hitl_actions"]
        ]
        _insert_many(
            target_conn,
            "cmir",
            "hitl_actions",
            list(hitl_actions_rows[0].keys()) if hitl_actions_rows else [],
            hitl_actions_rows,
        )

        pending_human_actions_rows = [
            {
                "id": pending_human_action_ids[r["id"]],
                "batch_id": r["batch_id"],
                "agent_run_id": agent_run_ids[r["agent_run_id"]],
                "thread_id": r["thread_id"],
                "email_id": r["email_id"],
                "po_line_id": r["po_line_id"],
                "interrupt_type": r["interrupt_type"],
                "payload": r["payload"],
                "state_snapshot": r["state_snapshot"],
                "status": r["status"],
                "answer": r["answer"],
                "actor": r["actor"],
                "created_at": r["created_at"],
                "completed_at": r["completed_at"],
            }
            for r in source["pending_human_actions"]
        ]
        _insert_many(
            target_conn,
            "cmir",
            "pending_human_actions",
            list(pending_human_actions_rows[0].keys()) if pending_human_actions_rows else [],
            pending_human_actions_rows,
        )

        agent_traces_rows = [
            {
                "id": agent_trace_ids[r["id"]],
                "run_id": agent_run_ids.get(r["run_id"]) if r["run_id"] else None,
                "batch_id": r["batch_id"],
                "thread_id": r["thread_id"],
                "node_name": r["node_name"],
                "status": r["status"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "duration_ms": r["duration_ms"],
                "input_snapshot": r["input_snapshot"],
                "output_snapshot": r["output_snapshot"],
                "error": r["error"],
            }
            for r in source["agent_traces"]
        ]
        _insert_many(
            target_conn,
            "cmir",
            "agent_traces",
            list(agent_traces_rows[0].keys()) if agent_traces_rows else [],
            agent_traces_rows,
        )

        po_line_errors_rows = [
            {**r, "agent_run_id": agent_run_ids.get(r["agent_run_id"]) if r["agent_run_id"] else None}
            for r in source["po_line_errors"]
        ]
        _insert_many(
            target_conn,
            "cmir",
            "po_line_errors",
            list(po_line_errors_rows[0].keys()) if po_line_errors_rows else [],
            po_line_errors_rows,
        )

        for table in CHECKPOINT_TABLES:
            rows = checkpoints[table]
            _insert_many(target_conn, "public", table, list(rows[0].keys()) if rows else [], rows)

        target_conn.commit()

    logger.info("Migration complete: %s", _target_row_counts(psycopg.connect(target_dsn)))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source-dsn", default=DEFAULT_SOURCE_DSN, help="Legacy cmir_db DSN (plain postgresql://)"
    )
    parser.add_argument(
        "--target-dsn", default=None, help="Override target DSN (default: settings.database.url)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report counts only, write nothing")
    parser.add_argument(
        "--force", action="store_true", help="Truncate non-empty target tables before importing"
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args()
    target_dsn = args.target_dsn or checkpoint_dsn(get_settings().database.url)
    migrate(args.source_dsn, target_dsn, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
