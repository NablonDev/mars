# PRD - CMIR Resolution Agent

Status: Current implementation contract  
Scope: Queue-based ingest, LangGraph workflow execution, and HITL review  
Audience: Product, backend, QA, and operations

## 1. Overview

The CMIR Resolution Agent ingests inbound CMIR emails, extracts draft CMIR data with Azure OpenAI, validates mandatory fields, pauses for human review when required, and persists approved CMIR records to Postgres.

The system is built around one reviewable workflow per email:

- One ingest request creates one `batch_id`
- One email creates one `agent_run_id`
- One email creates one `thread_id`
- Reviewer actions always target `thread_id`

## 2. Goals

- Support batch email ingest with isolated per-email workflow execution
- Expose a stable reviewer API using `thread_id`
- Preserve review state, history, and stage transitions in Postgres
- Support missing-fields and approval interrupts through LangGraph
- Persist LangGraph execution state in PostgreSQL so resume works across process restarts

## 3. Non-Goals

- Redesign of the core LangGraph workflow
- Changes to external reviewer API contracts

## 4. Identity Model

| Identifier | Meaning | Cardinality | Primary Use |
|---|---|---:|---|
| `batch_id` | One ingest request / queue batch grouping | 1 to many threads | Batch tracking |
| `agent_run_id` | One workflow execution for one email | 1 to 1 thread | Execution trace |
| `thread_id` | One reviewer-facing workflow thread | 1 to 1 run | Review, update, resume |
| `email_id` | Persisted source email row | Usually 1 to 1 thread | DB joins and idempotency |
| `source_message_id` | Source provider message id | Unique when available | Duplicate protection |

Rule: reviewer actions must use `thread_id`.

## 5. Current Architecture

### 5.1 HLD

```text
Gmail / IMAP
   -> FastAPI ingest API
   -> Postgres email_events (queue_status = new)
   -> Azure Function enqueuer
   -> Azure Service Bus
   -> Service Bus consumer
   -> FastAPI internal process-email API
   -> CMIRRunService
   -> LangGraph + PostgreSQL Checkpointer
   -> Postgres workflow + audit tables

Reviewer UI / client
   -> FastAPI reviewer APIs
   -> Same CMIRRunService
   -> Same LangGraph + PostgreSQL Checkpointer
```

### 5.2 Checkpointing Model

LangGraph now uses the official PostgreSQL checkpointer backed by the existing application PostgreSQL database. Checkpoints are keyed by the existing `thread_id`, so:

- queue-driven processing can interrupt in one process
- reviewer resume can continue later using the same `thread_id`
- restart and redeploy do not wipe LangGraph execution state

The Service Bus consumer remains a thin Azure Service Bus listener and HTTP forwarder to:

```http
POST /api/v1/internal/process-email
```

This keeps queue execution routed through the existing FastAPI process while checkpoint state is stored durably in PostgreSQL.

## 6. LLD

### 6.1 Core Modules

| Module | Responsibility |
|---|---|
| `cmir_agent/api.py` | FastAPI route contract |
| `cmir_agent/services.py` | Workflow orchestration and reviewer actions |
| `cmir_agent/workflow/graph.py` | LangGraph topology |
| `cmir_agent/workflow/nodes.py` | Node business steps |
| `cmir_agent/workers/service_bus_consumer.py` | Azure Service Bus listener and FastAPI forwarder |
| `cmir_agent/infrastructure/postgres_repositories.py` | Email and CMIR persistence |
| `cmir_agent/infrastructure/postgres_observability.py` | Runs, threads, pending actions, HITL history, trace persistence |
| `cmir_agent/container.py` | Dependency wiring and graph construction |
| `cmir_agent/infrastructure/service_bus.py` | Azure Service Bus queue sender |

### 6.2 LangGraph Flow

```text
persist_email
-> extract_cmir
-> validate_cmir
-> persist_ai_result
   -> collect_missing_fields (interrupt when required)
   -> human_approval      (interrupt when required)
      -> persist_cmir     (approve)
      -> persist_rejection (reject)
-> mark_email_read
-> END
```

### 6.3 Queue Processing Flow

1. Client calls `POST /api/v1/ingest/emails`
2. Matching emails are saved into `email_events` with queue metadata
3. Azure Function enqueues one Azure Service Bus message per email
4. `service_bus_consumer.py` receives the queue message
5. Consumer converts the message into the internal processing payload
6. Consumer calls `POST /api/v1/internal/process-email`
7. FastAPI processes one email through `CMIRRunService.process_queued_email(...)`
8. Consumer completes the queue message on success, abandons it on failure

### 6.4 HITL Flow

#### Missing Fields

1. Workflow interrupts in `collect_missing_fields`
2. Service creates one open `pending_human_actions` row
3. `workflow_threads` moves to `AWAITING_MISSING_FIELDS`
4. Reviewer submits `/threads/{thread_id}/missing-fields`
5. Service resumes LangGraph first
6. On success, reviewer action, thread transition, and next pending state are persisted atomically

#### Approval

1. Workflow interrupts in `human_approval`
2. Service creates one open `pending_human_actions` row
3. `workflow_threads` moves to `AWAITING_APPROVAL`
4. Reviewer either:
   - updates draft via `/update`
   - approves or rejects via `/decision`
5. Resume and post-resume state updates are persisted safely

## 7. Reviewer State Consistency Rules

The implementation must preserve the following invariants:

- At most one open `pending_human_actions` row per `thread_id`
- Reviewer action history is written to `hitl_actions`
- `workflow_threads` reflects the latest reviewer-visible stage
- A failed LangGraph resume must not close the open pending action first
- After successful resume, pending action completion, history logging, run status update, and thread transition must stay consistent

To support this, the implementation uses a transactional HITL state persistence path after successful resume, while LangGraph checkpoint state itself is stored independently by the PostgreSQL checkpointer.

## 8. Stage and Status Model

| UI Stage | `workflow_threads.status` | `current_node` |
|---|---|---|
| `INGESTING` | `running` | `persist_email` |
| `AWAITING_MISSING_FIELDS` | `waiting_missing_fields` | `collect_missing_fields` |
| `AWAITING_APPROVAL` | `waiting_approval` | `review_extracted_cmir` |
| `COMPLETED_APPROVED` | `completed_approved` | `null` |
| `COMPLETED_REJECTED` | `completed_rejected` | `null` |
| `FAILED` | `failed` | last attempted node |

## 9. Data Model

### 9.1 `agent_runs`

One row per workflow execution.

Important fields:

- `id`
- `batch_id`
- `thread_id`
- `email_id`
- `status`
- `current_node`
- `started_at`
- `updated_at`
- `completed_at`
- `error`

### 9.2 `workflow_threads`

One reviewer-facing row per email workflow.

Important fields:

- `thread_id`
- `batch_id`
- `agent_run_id`
- `email_id`
- `status`
- `stage`
- `current_node`
- `latest_snapshot`
- `pending_action_id`
- `updated_at`
- `error`

### 9.3 `pending_human_actions`

One open or completed HITL interrupt.

Important fields:

- `thread_id`
- `interrupt_type`
- `payload`
- `state_snapshot`
- `status`
- `answer`
- `actor`
- `created_at`
- `completed_at`

### 9.4 `hitl_actions`

Audit log of reviewer actions.

Important fields:

- `run_id`
- `batch_id`
- `email_id`
- `thread_id`
- `interrupt_type`
- `answer`
- `actor`
- `decision`
- `reason`
- `action_type`
- `field_changes`

### 9.5 `email_events`

Source email plus queue lifecycle state.

Important fields:

- `id`
- `source_message_id`
- `source_imap_id`
- `raw_content`
- `queue_status`
- `queue_message_id`
- `queue_delivery_count`
- `queue_error`
- `processed_at`

### 9.6 LangGraph Checkpoint Tables

LangGraph internal checkpoint tables are framework-managed and distinct from application business tables.

Rules:

- use official LangGraph PostgreSQL checkpointer tables only
- do not store execution state inside `email_events`, `agent_runs`, `hitl_actions`, or `cmir_records`
- initialize and verify checkpoint tables during startup

## 10. API Contract

Base path: `/api/v1`

### 10.1 Public APIs

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ingest/emails` | Persist matching emails for queue processing |
| `GET` | `/runs?view=threads` | Reviewer queue |
| `GET` | `/runs?view=agents` | Per-run operational view |
| `GET` | `/runs?view=batches` | Batch operational view |
| `GET` | `/threads/{thread_id}/stage` | Current thread stage |
| `GET` | `/threads/{thread_id}/snapshot` | Current draft and reviewer history |
| `POST` | `/threads/{thread_id}/missing-fields` | Submit missing mandatory fields |
| `POST` | `/threads/{thread_id}/update` | Save reviewer draft edits |
| `POST` | `/threads/{thread_id}/decision` | Approve or reject the draft |

### 10.2 Internal API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/internal/process-email` | Process one queued email inside FastAPI |

### 10.3 Request Models

#### `POST /ingest/emails`

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

#### `POST /threads/{thread_id}/missing-fields`

```json
{
  "actor": "reviewer@company.com",
  "fields": {
    "existing_cmir_ref": "CMIR-2026-001234"
  },
  "expected_updated_at": "2026-08-04T18:35:47.831784+05:30"
}
```

#### `POST /threads/{thread_id}/update`

```json
{
  "actor": "reviewer@company.com",
  "fields": {
    "brand": "Coastal",
    "site": "Site 12 - Rotterdam"
  },
  "expected_updated_at": "2026-08-04T18:35:47.831784+05:30"
}
```

#### `POST /threads/{thread_id}/decision`

```json
{
  "actor": "reviewer@company.com",
  "decision": "approve",
  "expected_updated_at": "2026-08-04T18:35:47.831784+05:30",
  "reason": ""
}
```

#### `POST /internal/process-email`

```json
{
  "batch_id": "queue_batch_b0a8883f-861f-427c-bb89-9548a1bdb772",
  "email_id": "b0a8883f-861f-427c-bb89-9548a1bdb772",
  "queue_message_id": "b0a8883f-861f-427c-bb89-9548a1bdb772",
  "email": {
    "email_id": "b0a8883f-861f-427c-bb89-9548a1bdb772",
    "imap_id": "12345",
    "sender": "bhupendra singh shekhawat <bpshekhawat08@gmail.com>",
    "subject": "Cmir",
    "body": "Please create or update the CMIR for the below material reference.",
    "source_message_id": "<message-id@mail.gmail.com>",
    "mark_read": true
  }
}
```

### 10.4 Response Shapes

#### Thread Stage

```json
{
  "batch_id": "queue_batch_01",
  "agent_run_id": 1042,
  "thread_id": "thread_01J4A",
  "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
  "source_message_id": "msg-001",
  "sender": "customer@example.com",
  "subject": "CMIR Request 1",
  "stage": "AWAITING_APPROVAL",
  "status": "waiting_approval",
  "current_node": "review_extracted_cmir",
  "pending_action_id": 3001,
  "updated_at": "2026-07-30T10:30:00+00:00"
}
```

#### Thread Snapshot

```json
{
  "batch_id": "queue_batch_01",
  "agent_run_id": 1042,
  "thread_id": "thread_01J4A",
  "email": {
    "email_id": "9c76f0b3-1e8d-4f31-9d17-15f42ad8f970",
    "sender": "customer@example.com",
    "subject": "CMIR Request 1",
    "source_message_id": "msg-001"
  },
  "stage": "AWAITING_APPROVAL",
  "editable_fields": [
    "sender_type",
    "customer_identity",
    "material_identity",
    "intent_phrase",
    "existing_cmir_ref",
    "brand",
    "site",
    "target_grd_code",
    "target_customer_material_ref",
    "effective_date",
    "reason"
  ],
  "cmir": {},
  "history": [],
  "updated_at": "2026-07-30T10:30:00+00:00"
}
```

## 11. Error Contract

```json
{
  "error": {
    "code": "THREAD_STALE",
    "message": "Thread was updated by another reviewer. Refresh snapshot and retry.",
    "details": {
      "thread_id": "thread_01J4A",
      "latest_updated_at": "2026-07-31T10:37:00Z"
    }
  }
}
```

| HTTP | Code | Meaning |
|---:|---|---|
| 404 | `THREAD_NOT_FOUND` | Unknown `thread_id` |
| 409 | `THREAD_NOT_WAITING` | Wrong resume/update action for current thread state |
| 409 | `THREAD_STALE` | `expected_updated_at` mismatch |
| 422 | `VALIDATION_ERROR` | Bad field name, bad payload, missing reject reason |
| 500 | `QUEUE_NOT_CONFIGURED` | Queue-backed ingest is unavailable |
| 500 | `WORKFLOW_RESUME_FAILED` | LangGraph or persistence failure during resume |

## 12. Configuration

Relevant environment variables:

```env
EMAIL_USERNAME=
EMAIL_PASSWORD=
IMAP_SERVER=
IMAP_PORT=993

DB_HOST=
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=

AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_VERSION=2024-08-01-preview
AZURE_OPENAI_DEPLOYMENT_NAME=

SERVICEBUS_FULLY_QUALIFIED_NAMESPACE=
SERVICE_BUS_QUEUE_NAME=mail-processing-queue
SERVICE_BUS_SESSION_ID=mail-processing
SERVICE_BUS_MAX_WAIT_SECONDS=30
SERVICE_BUS_LOCK_RENEW_SECONDS=300
QUEUE_ENQUEUE_BATCH_LIMIT=25
SERVICE_BUS_CONNECTION_STRING=

CMIR_AGENT_API_BASE_URL=http://127.0.0.1:8000
CMIR_AGENT_API_TIMEOUT_SECONDS=30
```

## 13. Acceptance Criteria

- Ingest API persists queueable email rows and returns batch metadata
- Azure Service Bus consumer forwards one email at a time to FastAPI internal processing
- One `thread_id` exists per reviewable email workflow
- Reviewer APIs operate only on `thread_id`
- Missing-fields and approval interrupts create exactly one open pending action
- Resume failures do not prematurely close the reviewer’s open pending action
- Successful reviewer actions update thread stage, run state, and HITL history consistently
- Approved flows persist CMIR records without duplicate inserts for the same `email_id`
- LangGraph checkpoints survive process restarts and continue to resume by `thread_id`

## 14. Known Constraints

- Azure Service Bus queueing is used for asynchronous email processing and retry
- Queue forwarding still depends on FastAPI availability for internal processing
- Business state and LangGraph checkpoint state are intentionally stored separately
