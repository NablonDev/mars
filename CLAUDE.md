# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

FastAPI + LangGraph backend that ingests inbound CMIR emails, extracts CMIR draft data with Azure OpenAI, pauses for human-in-the-loop (HITL) review, and persists approved records in Postgres. Deploys as an Azure Function App (`function_app.py`) alongside the FastAPI process. The authoritative spec is `docs/prd.md` — read it before making changes to workflow/queue/reviewer behavior; it documents the identity model, stage/status mapping, API contract, and data model in detail.

## Commands

```bash
# Install
pip install -r requirements.txt

# Run the API
uvicorn cmir_agent.api:app --reload
# -> http://127.0.0.1:8000, docs at /docs

# Run the legacy CLI entry point (superseded by the API; queue/HITL flows require the API)
python -m cmir_agent.main

# Run all tests
python -m unittest discover -s tests

# Run a single test file / case
python -m unittest tests.test_services
python -m unittest tests.test_services.SomeTestCase.test_something

# Apply DB schema changes
psql -d cmir_db -f migrations/schema.sql
```

Tests use fakes at the API/service/repository boundaries and must not require live Gmail, Azure OpenAI, or Postgres connections.

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
  -> cmir_agent/workers/service_bus_consumer.py (thin listener, forwards via HTTP)
  -> POST /api/v1/internal/process-email (FastAPI)
  -> CMIRRunService -> LangGraph (PostgreSQL checkpointer) -> Postgres workflow/audit tables

Reviewer UI -> FastAPI reviewer APIs -> same CMIRRunService -> same LangGraph graph instance
```

The Service Bus consumer never runs workflow logic itself — it deserializes the queue message and forwards it over HTTP so all execution goes through one FastAPI process. The Azure Function only claims new rows from Postgres and enqueues them; it does not process emails.

### Checkpointing

LangGraph uses the official `PostgresSaver` checkpointer (see `cmir_agent/container.py`), keyed by `thread_id`, stored in the same application Postgres database. This lets queue-driven processing interrupt in one process and reviewer resume continue later in a different request, and survives restarts/redeploys. LangGraph checkpoint tables are framework-owned — never store execution state in `email_events`, `agent_runs`, `hitl_actions`, or `cmir_records`; business state (thread status, pending actions, audit log) is intentionally persisted separately from graph checkpoint state, and both must be kept consistent (see PRD §7 for the exact invariants around `pending_human_actions`).

### Layering (ports & adapters)

- `interfaces/` — abstract ports (`EmailReader`, `CMIRExtractor`, repositories, `HumanReviewPort`).
- `infrastructure/` — concrete adapters implementing those ports (Gmail IMAP, Azure OpenAI, SQLAlchemy/Postgres repositories, Service Bus).
- `domain/` — framework-free models (`CMIR`, `EmailMessage`, `WorkflowThread`) and mandatory-field validation.
- `workflow/` — LangGraph state (`state.py`), node functions (`nodes.py`), graph topology (`graph.py`), per-node trace logging (`tracing.py`).
- `container.py` — single composition root; `Container.build()` is a process-wide singleton that wires config, adapters, the compiled graph, and the checkpointer. Use `Container.build()` rather than constructing adapters directly.
- `services.py` — orchestration layer between the API and the graph (`CMIRRunService`); owns reviewer state-transition rules and the resume-then-persist transaction.
- `api.py` — route contract only; delegates to `services.py`.

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

Env vars are loaded once into typed, frozen dataclasses in `config.py` (`EmailConfig`, `DatabaseConfig`, `LLMConfig`, `ServiceBusConfig`) rather than read ad hoc via `os.getenv` elsewhere — add new settings there. Required at minimum: `EMAIL_USERNAME`/`EMAIL_PASSWORD`/`IMAP_SERVER`, `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`, `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_DEPLOYMENT_NAME`. Service Bus settings have working defaults for local dev but need `SERVICEBUS_FULLY_QUALIFIED_NAMESPACE`/`SERVICE_BUS_CONNECTION_STRING` for real queue use.

`migrations/schema.sql` assumes pre-existing base tables and only layers on the PRD thread model (`workflow_threads`, `pending_human_actions`, thread columns on `agent_runs`/`hitl_actions`/`email_events`) — the pre-PRD base schema isn't in the repo.
