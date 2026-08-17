# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

FastAPI + LangGraph backend that ingests inbound CMIR emails, extracts CMIR draft data with Azure OpenAI, pauses for human-in-the-loop (HITL) review, and persists approved records in Postgres. A second agent, PO Validation, shares the same FastAPI app, Postgres connection, and checkpointer. Deploys as an Azure Function App (`function_app.py`) alongside the FastAPI process. The authoritative spec is `docs/prd.md` — read it before making changes to workflow/queue/reviewer behavior; it documents the identity model, stage/status mapping, API contract, and data model in detail.

## Commands

```bash
# Install (either works; uv is the source of truth, requirements.txt is kept in sync for Azure Functions deployment)
uv sync
# or: pip install -r requirements.txt

# Run the API
uvicorn app.main:app --reload
# -> http://127.0.0.1:8000, docs at /docs

# Run the legacy CLI entry point (superseded by the API; queue/HITL flows require the API)
python scripts/run_cli_ingest.py

# Run all tests
python -m unittest discover -s tests

# Run only unit tests (no live dependencies required)
python -m unittest discover -s tests/unit

# Run integration tests (requires a reachable Postgres; skips gracefully if none found)
docker compose up -d postgres
python -m unittest discover -s tests/integration

# Apply DB schema changes
alembic upgrade head

# Local full stack via Docker
docker compose up -d --build
```

A `Makefile` wraps the common ones (`make install`, `make run`, `make test`, `make test-unit`, `make test-integration`, `make migrate`, `make docker-up`).

Tests use fakes at the API/service/repository boundaries and must not require live Gmail, Azure OpenAI, or Postgres connections — except `tests/integration/`, which deliberately does exercise a real Postgres connection and skips (not fails) when one isn't reachable.

## Identity Model — read before touching reviewer/queue code

- `batch_id`: one ingest request, groups many threads.
- `agent_run_id`: one workflow execution for one email (1:1 with thread).
- `thread_id`: one reviewer-facing workflow thread — **the only identifier reviewer/UI actions may key on**. Never use sender email, `batch_id`, `agent_run_id`, or `email_id` as the review key, since one batch holds multiple emails and multiple emails can share a sender.
- `email_id`: persisted source email row, used for DB joins/idempotency.
- `source_message_id`: source provider message id, used for duplicate protection.

## Architecture

### Request/queue flow

```
Gmail/IMAP -> FastAPI ingest API -> Postgres email_events (queue_status=new)
  -> Azure Function timer trigger (function_app.py: enqueue_new_mail, runs every minute)
  -> Azure Service Bus
  -> app/workers/service_bus_consumer.py (thin listener, forwards via HTTP)
  -> POST /api/v1/internal/process-email (FastAPI)
  -> CMIRRunService -> LangGraph (PostgreSQL checkpointer) -> Postgres workflow/audit tables

Reviewer UI -> FastAPI reviewer APIs -> same CMIRRunService -> same LangGraph graph instance
```

The Service Bus consumer never runs workflow logic itself — it deserializes the queue message and forwards it over HTTP so all execution goes through one FastAPI process. The Azure Function only claims new rows from Postgres and enqueues them; it does not process emails.

### Checkpointing

LangGraph uses the official `PostgresSaver` checkpointer (see `app/core/container.py`), keyed by `thread_id`, stored in the same application Postgres database. This lets queue-driven processing interrupt in one process and reviewer resume continue later in a different request, and survives restarts/redeploys. LangGraph checkpoint tables are framework-owned — never store execution state in `email_events`, `agent_runs`, `hitl_actions`, or `cmir_records`; business state (thread status, pending actions, audit log) is intentionally persisted separately from graph checkpoint state, and both must be kept consistent (see PRD §7 for the exact invariants around `pending_human_actions`).

### Layering

- `app/schemas/` — Pydantic API request/response DTOs, plus the internal value objects/graph-state shapes that flow through LangGraph (`CMIR`, `EmailMessage`, `WorkflowThread`, `PendingHumanAction`, `PoLine`, `MaterialMasterRecord`, `PoLineError`) and mandatory-field constants.
- `app/models/` — SQLAlchemy ORM models only (`app/models/__init__.py` imports every submodule so Alembic's autogenerate sees the full `Base.metadata`).
- `app/repositories/` — concrete Postgres-backed repository classes (email, CMIR, action log, observability, PO validation). No abstract port layer — repositories are constructed directly against `app.db.session.Database` and duck-typed by tests, not inherited from an interface.
- `app/services/` — business orchestration (`CMIRRunService`, `PoValidationService`), framework-free domain rules (`cmir_validation.py`, `cmir_merge.py`, `identity.py`), and external-service clients (`email_reader.py` Gmail IMAP, `cmir_extractor.py` Azure OpenAI, `cli_human_review.py` console I/O for the legacy CLI job).
- `app/agents/` — LangGraph state (`state.py`), node functions (`nodes.py`), and graph topology (`graph.py`) for each agent (`app/agents/cmir/`, `app/agents/po_validation/`), plus the shared per-node trace logging decorator in `app/core/tracing.py`.
- `app/api/` — route contracts only (`app/api/v1/cmir.py`, `app/api/v1/po_validation.py`), aggregated by `app/api/router.py`; `app/api/dependencies.py` builds and memoizes the per-app-instance service singletons FastAPI's `Depends()` resolves against.
- `app/core/container.py` — single composition root; `Container.build()` is a process-wide singleton that wires config, adapters, both compiled graphs, and the shared checkpointer. Use `Container.build()` rather than constructing adapters directly.
- `app/main.py` — FastAPI app factory (`create_app`) and the ASGI entrypoint (`app = create_app()`) `uvicorn app.main:app` runs.
- `app/queue/`, `app/workers/`, `app/jobs/` — Service Bus producer, the standalone Service Bus consumer process, and the legacy CLI ingest job, respectively.

### LangGraph node sequence

```
persist_email -> extract_cmir -> validate_cmir -> persist_ai_result
  -> collect_missing_fields (interrupt if required fields missing)
  -> human_approval (interrupt for reviewer approval)
     -> persist_cmir (approve) | persist_rejection (reject)
  -> mark_email_read -> END
```

### Stage/status mapping

Reviewer-facing `stage` and internal `workflow_threads.status`/`current_node` are distinct vocabularies — see PRD §8 for the full table (`AWAITING_MISSING_FIELDS` ↔ `waiting_missing_fields`, `AWAITING_APPROVAL` ↔ `waiting_approval`, etc.). Missing-fields and update/decision APIs check this status and return `THREAD_NOT_WAITING` if it doesn't match, and `THREAD_STALE` on an `expected_updated_at` mismatch (optimistic concurrency).

## Configuration

Env vars are loaded once into typed, frozen dataclasses in `app/core/config.py` (`EmailConfig`, `DatabaseConfig`, `LLMConfig`, `ServiceBusConfig`) rather than read ad hoc via `os.getenv` elsewhere — add new settings there. Required at minimum: `EMAIL_USERNAME`/`EMAIL_PASSWORD`/`IMAP_SERVER`, `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`, `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_DEPLOYMENT_NAME`. Service Bus settings have working defaults for local dev but need `SERVICEBUS_FULLY_QUALIFIED_NAMESPACE`/`SERVICE_BUS_CONNECTION_STRING` for real queue use. Copy `.env.example` to `.env` and fill in real values — never commit `.env`.

## Database migrations

Schema changes are managed by Alembic (`alembic/`), targeting `app.db.base.Base.metadata` (populated via `app/models/__init__.py`, which imports every ORM module). Revisions `0001`-`0005` under `alembic/versions/` port the project's pre-Alembic `migrations/schema.sql` history (now removed — see git history if the original raw SQL is ever needed) faithfully, including its `DO $$...$$` conditional blocks, partial unique indexes, and the SCD2 `ROW_NUMBER() OVER (PARTITION BY ...)` backfill, all kept as raw `op.execute()` SQL rather than re-expressed declaratively, to guarantee behavioral parity.

**Already-deployed environments**: their base tables (`agent_runs`, `email_events`, `hitl_actions`, `cmir_records`, `email_action_log`, `agent_trace`) already exist from before this repo's Alembic conversion. Do **not** run `alembic upgrade` from scratch there — run `alembic stamp head` (or `alembic stamp 0001` if only the base tables exist and 0002-0005 haven't been applied yet) so Alembic's revision tracking starts from the correct point without re-running DDL against live tables. `alembic upgrade head` from an empty database is only for genuinely fresh environments (local dev, CI, a new deployment).

New schema changes: `alembic revision -m "description"` (or `--autogenerate` once the ORM models are updated), then `alembic upgrade head`.
