# Database Reference

Postgres schema for the CMIR Resolution Agent and PO Validation Agent. ORM models live
in `app/models/` (split by aggregate, all sharing one `Base` from `app/db/base.py`);
schema changes are managed by Alembic (`alembic/versions/`). See `CLAUDE.md`'s Database
migrations section for the fresh-install-vs-already-deployed distinction before running
`alembic upgrade`.

## Migration history

| Revision | File | What it adds |
|---|---|---|
| `0001` | `alembic/versions/0001_base_tables.py` | Bootstraps `agent_runs`, `email_events`, `hitl_actions`, `cmir_records`, `email_action_log`, `agent_trace` for a fresh database |
| `0002` | `alembic/versions/0002_hitl_thread_model.py` | `batch_id`/`thread_id`/queue-lifecycle columns; `workflow_threads`, `pending_human_actions` tables |
| `0003` | `alembic/versions/0003_po_validation.py` | PO Validation tables (`po_lines`, `material_master`, `po_line_errors`); nullable `email_id` + `po_line_id` columns on the shared tables |
| `0004` | `alembic/versions/0004_cmir_scd2.py` | SCD2 versioning on `cmir_records` (`is_current`, `valid_to`, `superseded_by_id`; `approved_at` renamed to `valid_from`) |
| `0005` | `alembic/versions/0005_identity_normalization.py` | Format-insensitive identity matching keys (`customer_identity_key`, `target_customer_material_ref_key`) |

Already-deployed environments should `alembic stamp head` rather than running `alembic
upgrade` from scratch — their base tables predate this repo's Alembic conversion.

## Tables

### `email_events` (`app/models/email.py::EmailEventORM`)
One row per inbound email, doubling as the Service Bus queue's work item.

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `sender`, `subject`, `raw_content` | Raw email content |
| `source_message_id`, `source_imap_id` | Duplicate-detection keys |
| `extracted_json`, `missing_fields`, `status` | Latest CMIR extraction result |
| `queue_status` | `new` → `enqueueing` → `queued` → `processing` → `processed` / `failed` |
| `queue_message_id`, `queue_delivery_count`, `queue_error` | Service Bus delivery bookkeeping |

Indexes: unique partial on `source_message_id` (`WHERE ... IS NOT NULL`), unique
partial on `queue_message_id`, `(queue_status, created_at)` for the enqueuer's FIFO scan.

### `email_action_log` (`EmailActionLogORM`)
Append-only audit log of email-level workflow events (`Email Received`, `Extraction
Complete`, `CMIR Created`, etc.). Write-only from the app's perspective — no read path
in the API today.

### `cmir_records` (`app/models/cmir.py::CMIRRecordORM`)
SCD2 history of approved CMIR mappings — **not** an append-only log. Exactly one
`is_current = TRUE` row per `(customer_identity_key, target_customer_material_ref_key)`,
enforced by a partial unique index (the actual correctness guarantee — see
`app/repositories/cmir.py::PostgresCMIRRepository.supersede_and_insert`, which retires
the old row and inserts the new one in one transaction, catching the index violation as
`CMIRVersionConflict` on a lost race).

| Column | Notes |
|---|---|
| `email_id` | Nullable — a PO-Validation-triggered manual mapping has no source email |
| `customer_identity`, `target_customer_material_ref`, ... | Raw, as-submitted values (display/audit) |
| `customer_identity_key`, `target_customer_material_ref_key` | Format-insensitive lookup keys (`app/services/identity.py::normalize_identity_key` — keep the Python regex and this table's backfill SQL in sync if the rule ever changes) |
| `is_current`, `valid_from`, `valid_to`, `superseded_by_id` | SCD2 versioning fields |

Written by: CMIR agent's `persist_cmir` node (email-driven approval) and PO
Validation's `create_cmir_record` node (manual mapping). Read by: PO Validation's
`validate_against_cmir` node (`find_latest_for_customer_material`) — **shared table,
shared repository class, two agents.**

### `agent_runs` (`app/models/observability.py::AgentRunORM`)
One row per independent agent execution (one email, or one PO line). `run_type`
(`email_ingest` vs `PO_VALIDATION`) and the nullable `email_id`/`po_line_id` columns
distinguish which agent owns a given run. `batch_id` groups every run created by one
ingest request.

### `workflow_threads` (`WorkflowThreadORM`)
The reviewer-facing thread — **the only identifier reviewer/UI actions may key on**
(see `CLAUDE.md`'s Identity Model). Shared by both agents, distinguished by whether
`email_id` or `po_line_id` is populated. `latest_snapshot` (JSON) carries the CMIR
draft/diff or PO-line candidate shown to the reviewer. For PO Validation, a row is only
inserted the first time a line actually interrupts — the fully-automatic path never
gets one.

### `pending_human_actions` (`PendingHumanActionORM`)
Open/completed human-review interrupts, one per thread at a time — enforced by a
unique partial index (`WHERE status = 'open'`). `interrupt_type` distinguishes which
reviewer action is expected next (`missing_mandatory_fields`, `approval_required`,
`manual_cmir_entry`, `qty_mismatch_decision`).

### `hitl_actions` (`HITLActionORM`)
Audit trail of every reviewer answer/decision, written transactionally alongside the
`pending_human_actions` transition by
`app/repositories/observability.py::PostgresHITLStateRepository.apply_human_action` —
one atomic operation closes the old pending action, logs this row, opens the next
pending action (if any), and updates `workflow_threads`/`agent_runs`.

### `agent_trace` (`AgentTraceORM`)
One row per LangGraph node execution (`completed` / `paused` / `failed`), written by
the `traced()` decorator (`app/core/tracing.py`) wrapping every node in both graphs.
Operational/debugging surface only — not read by the API.

### `po_lines` (`app/models/po_validation.py::PoLineORM`)
One row per PO line submitted for validation. `status` lifecycle: `NEW` →
`VALIDATING` → (`AWAITING_DECISION` |) → `READY_FOR_SO_CREATION` /
`READY_FOR_SO_CREATION_PARTIAL` / `DISCONTINUED` / `FAILED`.

### `material_master` (`MaterialMasterORM`)
Local mirror of SAP MARC fields, keyed by `(sap_material_number, plant)` (unique
constraint). Populated by an out-of-scope external sync process — this repo only reads
it (`check_material_master` node).

### `po_line_errors` (`PoLineErrorORM`)
System/lookup failures on a PO line — distinct from `hitl_actions` (human decisions).
Written by the `handle_error` node.

### LangGraph checkpoint tables
Created and owned entirely by `PostgresSaver.setup()` (`app/core/container.py`) — never
created via Alembic and never queried/written directly by application code. Both
agents' graphs share one `PostgresSaver` instance; checkpoint `thread_id`s are
namespaced (`thread_po_...` for PO Validation) so the two graphs' checkpoints never
collide.

## Cross-agent sharing summary

| Shared by both agents | CMIR-agent-only | PO-Validation-only |
|---|---|---|
| `agent_runs`, `workflow_threads`, `pending_human_actions`, `hitl_actions`, `agent_trace`, `cmir_records` | `email_events`, `email_action_log` | `po_lines`, `material_master`, `po_line_errors` |
