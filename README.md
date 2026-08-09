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
cmir_agent/
  api.py                         FastAPI app and /api/v1 routes
  schemas.py                     Pydantic request/response schemas
  services.py                    API workflow orchestration and business checks
  container.py                   Dependency injection composition root
  config.py                      Environment-based typed config
  main.py                        Legacy CLI entry point

  domain/
    models.py                    CMIR, EmailMessage, WorkflowThread models
    validators.py                Mandatory-field validation

  interfaces/
    email_reader.py              EmailReader port
    extractor.py                 CMIRExtractor port
    repositories.py              Email/CMIR repository ports
    observability.py             Run/thread/pending-action repository ports
    human_review.py              CLI human review port

  infrastructure/
    database.py                  SQLAlchemy engine/session factory
    orm_models.py                SQLAlchemy ORM models
    gmail_email_reader.py        Gmail IMAP adapter
    azure_openai.py              Azure OpenAI CMIR extractor
    postgres_repositories.py     SQLAlchemy email/CMIR repositories
    postgres_observability.py    SQLAlchemy run/thread/audit repositories
    cli_human_review.py          Legacy console review adapter

  workflow/
    state.py                     LangGraph state shape
    nodes.py                     LangGraph node functions
    graph.py                     LangGraph topology
    tracing.py                   Per-node trace logging

docs/
  prd.md                         Product requirements
  coding_guide.md                Mandatory engineering standards
  debug_flow.html                Debugging guide

migrations/
  schema.sql                     Postgres schema changes

tests/
  test_api.py
  test_services.py
  test_sqlalchemy_repositories.py
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

```bash
psql -d cmir_db -f migrations/schema.sql
```

The migration defines the PRD thread model:

- `workflow_threads`
- `pending_human_actions`
- `source_message_id` on `email_events`
- `batch_id`, `thread_id`, and `email_id` on `agent_runs`
- thread audit fields on `hitl_actions`

Note: `migrations/schema.sql` currently assumes the pre-existing base tables exist. It includes a TODO because the authoritative pre-PRD base schema is not documented in the repo.

## Running the API

Start the FastAPI server:

```bash
uvicorn cmir_agent.api:app --reload
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
python -m cmir_agent.main
```

Use the API for PRD thread-based review flows.

## Running Tests

```bash
python -m unittest discover -s tests
```

The tests use fakes for API, service, and repository boundaries. They should not require real Gmail, Azure OpenAI, or Postgres connections.

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

## Security

Never commit `.env` or real credentials. Rotate any Gmail app password, database password, or Azure OpenAI key that has been shared outside a secure secret manager.
