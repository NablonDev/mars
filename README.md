# CMIR Resolution Agent

FastAPI + LangGraph backend for processing inbound CMIR emails, extracting CMIR draft data with Azure OpenAI, pausing for human review, and persisting approved records in Postgres through SQLAlchemy.

The current implementation follows `docs/prd.md`: one backend ingest batch can process many emails, and each email-derived review workflow gets its own UI-facing `thread_id`.

## What This App Does

1. Reads matching emails from Gmail IMAP.
2. Creates one `batch_id` for the ingest request.
3. Creates one `agent_runs` row per email agent execution.
4. Creates one independent `workflow_threads` row per email.
5. Runs each email through a LangGraph workflow.
6. Extracts CMIR fields with Azure OpenAI.
7. Validates mandatory fields.
8. Pauses for human input when fields are missing or approval is required.
9. Resumes a specific workflow using `thread_id`.
10. Writes approved CMIR records to Postgres.
11. Logs reviewer actions and graph traces for debugging.

## Important Identity Rule

Reviewer/UI actions must use `thread_id`.

Do not use sender email, `batch_id`, `agent_run_id`, or `email_id` as the review workflow key. One batch can contain multiple emails, and multiple emails can come from the same sender.

## Project Layout

```text
app/
  main.py                        FastAPI app factory + ASGI entrypoint
  core/
    config.py                    Environment-based typed config
    container.py                 Dependency injection composition root
    exceptions.py                ServiceError (PRD API error contract)
    tracing.py                   Per-node LangGraph trace logging
  api/
    dependencies.py              Lazy per-app-instance service singletons
    router.py                    Aggregates all /api/v1 routers
    v1/
      cmir.py                    CMIR routes
      po_validation.py           PO Validation routes
  db/
    base.py                      SQLAlchemy DeclarativeBase
    session.py                   SQLAlchemy engine/session factory
  models/                        SQLAlchemy ORM models (email, cmir, observability, po_validation)
  schemas/                       Pydantic DTOs + internal value objects/graph-state shapes
  repositories/                  Concrete Postgres repositories
  services/
    cmir_run_service.py          CMIR API workflow orchestration
    po_validation_service.py     PO Validation API workflow orchestration
    cmir_validation.py           Mandatory-field validation
    cmir_merge.py                SCD2-style field merge
    identity.py                  Identity-key normalization
    email_reader.py              Gmail IMAP client
    cmir_extractor.py            Azure OpenAI CMIR extractor
    cli_human_review.py          Legacy console review adapter
  agents/
    cmir/                        LangGraph state, nodes, graph for the CMIR agent
    po_validation/                LangGraph state, nodes, graph for the PO Validation agent
  queue/
    producer.py                  Azure Service Bus producer
  workers/
    service_bus_consumer.py      Standalone Service Bus consumer process
  jobs/
    cli_ingest.py                Legacy CLI entry point

scripts/
  run_cli_ingest.py              Thin wrapper for app/jobs/cli_ingest.py

alembic/
  versions/                      Schema migrations (0001-0005)

docs/
  prd.md                         Product requirements
  coding_guide.md                Mandatory engineering standards
  debug_flow.html                Debugging guide

tests/
  unit/                          Fakes/mocks only, no live dependencies
  integration/                   Real Postgres connection (skips if unreachable)
```

## Main API Endpoints

Base path: `/api/v1`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ingest/emails` | Start one email ingest batch. |
| `GET` | `/runs?view=threads` | Reviewer queue: list workflow threads. |
| `GET` | `/runs?view=agents` | Ops view: list per-email agent runs. |
| `GET` | `/runs?view=batches` | Ops view: list batch rollups by `batch_id`. |
| `GET` | `/threads/{thread_id}/stage` | Get current stage/status for one thread. |
| `GET` | `/threads/{thread_id}/snapshot` | Get email, CMIR draft, and HITL history. |
| `POST` | `/threads/{thread_id}/missing-fields` | Submit missing mandatory fields and resume graph. |
| `POST` | `/threads/{thread_id}/update` | Save reviewer draft edits. |
| `POST` | `/threads/{thread_id}/decision` | Approve or reject a draft. |

## Ingest Request Example

```json
{
  "max_workers": 4,
  "source": "gmail",
  "filters": {
    "subject_contains": "CMIR",
    "unread_only": true
  }
}
```

Field behavior:

| Field | Meaning |
|---|---|
| `max_workers` | Controls how many independent email workflows are processed in parallel. |
| `source` | Currently only `gmail` is supported. |
| `filters.subject_contains` | Request-scoped subject search override for Gmail IMAP. |
| `filters.unread_only` | When true, only unread emails are searched. |

## Environment Setup

### 1. Install Python

Use Python 3.11 or newer.

Check that Python is available:

```bash
python --version
```

On Windows, if `python` is not found, install Python from python.org and enable "Add Python to PATH" during installation.

### 2. Create a Virtual Environment

Windows PowerShell:

```powershell
python -m venv myvenv
.\myvenv\Scripts\Activate.ps1
```

If PowerShell blocks activation, allow scripts for the current user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

macOS/Linux:

```bash
python -m venv myvenv
source myvenv/bin/activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Or, if you have [uv](https://docs.astral.sh/uv/) installed (the project's `pyproject.toml`/`uv.lock` are the source of truth; `requirements.txt` is kept in sync for Azure Functions deployment):

```bash
uv sync
```

### 4. Create `.env`

Create a `.env` file in the project root. This file is ignored by git.

```env
EMAIL_USERNAME=your_gmail_address@example.com
EMAIL_PASSWORD=your_gmail_app_password
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993

DB_HOST=localhost
DB_PORT=5432
DB_NAME=cmir_db
DB_USER=postgres
DB_PASSWORD=your_postgres_password

AZURE_OPENAI_API_KEY=your_azure_openai_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-08-01-preview
AZURE_OPENAI_DEPLOYMENT_NAME=your_deployment_name
```

Gmail note: use a Gmail app password, not your normal Gmail password.

## Database Setup

### 1. Create the Database

Example with `psql`:

```bash
createdb cmir_db
```

Or inside `psql`:

```sql
CREATE DATABASE cmir_db;
```

### 2. Apply Schema

Schema changes are managed by [Alembic](https://alembic.sqlalchemy.org/):

```bash
alembic upgrade head
```

Revisions `0001`-`0005` under `alembic/versions/` define the PRD thread model:

- `0001`: bootstraps the base tables (`agent_runs`, `email_events`, `hitl_actions`, `cmir_records`, `email_action_log`, `agent_trace`) for a genuinely fresh database
- `0002`: `workflow_threads`, `pending_human_actions`, `source_message_id` on `email_events`, `batch_id`/`thread_id`/`email_id` on `agent_runs`, thread audit fields on `hitl_actions`
- `0003`: PO Validation Agent tables (`po_lines`, `material_master`, `po_line_errors`) and shared `po_line_id` columns
- `0004`: SCD2 CMIR versioning (`is_current`, `valid_to`, `superseded_by_id`)
- `0005`: format-insensitive identity matching keys

**If you already have a database from before this repo used Alembic**, its base tables already exist — do not run `alembic upgrade` from scratch against it. Run `alembic stamp head` instead so Alembic's revision tracking starts from the correct point without re-running DDL against live tables. See `CLAUDE.md`'s Database migrations section for the full explanation.

## Running the API

Start the FastAPI server:

```bash
uvicorn app.main:app --reload
```

Default local URL:

```text
http://127.0.0.1:8000
```

Open generated API docs:

```text
http://127.0.0.1:8000/docs
```

Start an ingest batch:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/ingest/emails" ^
  -H "Content-Type: application/json" ^
  -d "{\"max_workers\":4,\"source\":\"gmail\",\"filters\":{\"subject_contains\":\"CMIR\",\"unread_only\":true}}"
```

PowerShell alternative:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/ingest/emails" `
  -ContentType "application/json" `
  -Body '{"max_workers":4,"source":"gmail","filters":{"subject_contains":"CMIR","unread_only":true}}'
```

List reviewer queue:

```text
GET http://127.0.0.1:8000/api/v1/runs?view=threads
```

List waiting approval threads:

```text
GET http://127.0.0.1:8000/api/v1/runs?view=threads&status=waiting_approval
```

## Running the Legacy CLI

The API is the current primary path. The legacy CLI entry point still exists:

```bash
python scripts/run_cli_ingest.py
```

Use the API for PRD thread-based review flows.

## Running Tests

```bash
python -m unittest discover -s tests
```

Or split by kind:

```bash
python -m unittest discover -s tests/unit          # fakes/mocks only, no live dependencies
python -m unittest discover -s tests/integration   # real Postgres; skips (not fails) if unreachable
```

`tests/unit/` uses fakes for API, service, and repository boundaries and never requires real Gmail, Azure OpenAI, or Postgres connections. `tests/integration/` deliberately exercises a real Postgres connection and session — run `docker compose up -d postgres` first, or point `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` at any reachable Postgres.

## Debugging Guide

Open this file in a browser:

```text
docs/debug_flow.html
```

It explains:

- Complete ingest flow
- Where `max_workers` and email filters are used
- LangGraph node sequence
- Reviewer API flow
- Stage/status mapping
- Database tables and debug queries
- Common failure scenarios

## Common Issues

### `python` is not recognized

Install Python and make sure it is added to PATH. Then recreate the virtual environment:

```powershell
Remove-Item -Recurse -Force .\myvenv
python -m venv myvenv
.\myvenv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Virtualenv points to a missing Python

Delete and recreate the virtualenv. A venv stores an absolute path to the Python executable that created it.

### Gmail returns no emails

Check:

- `filters.subject_contains`
- `filters.unread_only`
- Gmail app password
- IMAP is enabled
- `EmailConfig.lookback_days`
- emails are actually unread when `unread_only` is true

### Thread update returns `THREAD_STALE`

Fetch the latest stage or snapshot and retry with the latest `updated_at` value as `expected_updated_at`.

### Thread update returns `THREAD_NOT_WAITING`

Check `workflow_threads.status`.

- Missing fields API requires `waiting_missing_fields`.
- Update and decision APIs require `waiting_approval`.

### Decision returns `CMIR_VERSION_CONFLICT`

Another thread's approval already superseded the active `cmir_records` row for this
customer/material while this thread was waiting for a decision (HTTP 409). The thread
closes to `COMPLETED_CONFLICT`/`completed_conflict` — it does not reopen for retry
automatically. Fetch `GET /threads/{thread_id}/snapshot` for another thread against
the same customer/material, or start a new one, to see and act on the record that
actually won.

## Security

Never commit `.env` or real credentials. Rotate any Gmail app password, database password, or Azure OpenAI key that has been shared outside a secure secret manager.
