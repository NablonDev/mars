-- ============================================================
-- CMIR HITL batch_id + per-email agent_run_id thread model
-- ============================================================
-- ------------------------------------------------------------
-- LangGraph Internal Checkpoint Tables
-- ------------------------------------------------------------
-- LangGraph PostgreSQL Checkpointer manages its own checkpoint tables.
-- They are created automatically by PostgresSaver.setup() during startup.
-- Do not create custom replacement tables here.
--
-- Framework-managed tables are intentionally kept separate from the
-- application business tables below.
-- ------------------------------------------------------------

-- ============================================================
-- Application Business Tables
-- ============================================================
-- This migration assumes the existing base tables already exist:
-- agent_runs, email_events, hitl_actions, cmir_records, email_action_log.
-- TODO: Add full base-table DDL once the authoritative base schema is
-- documented. The current migration only specifies incremental changes.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE email_events
ADD COLUMN IF NOT EXISTS source_message_id TEXT;

ALTER TABLE email_events
ADD COLUMN IF NOT EXISTS source_imap_id TEXT,
ADD COLUMN IF NOT EXISTS queue_status TEXT NOT NULL DEFAULT 'new',
ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS queue_message_id TEXT,
ADD COLUMN IF NOT EXISTS queue_delivery_count INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS queue_error TEXT;

ALTER TABLE email_events
ALTER COLUMN created_at SET DEFAULT NOW(),
ALTER COLUMN updated_at SET DEFAULT NOW();

-- Existing email_events predate the queue lifecycle and must not be picked up
-- by the Service Bus enqueuer after this migration is applied.
UPDATE email_events
SET queue_status = 'processed',
    processed_at = COALESCE(processed_at, updated_at, NOW())
WHERE queue_status = 'new'
  AND queue_message_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_email_events_source_message_id
ON email_events(source_message_id)
WHERE source_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_email_events_queue_status_created_at
ON email_events(queue_status, created_at ASC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_email_events_queue_message_id
ON email_events(queue_message_id)
WHERE queue_message_id IS NOT NULL;

-- agent_runs now represents one per-email agent execution. batch_id groups
-- all agent runs created by one POST /api/v1/ingest/emails request.
ALTER TABLE agent_runs
ADD COLUMN IF NOT EXISTS batch_id TEXT,
ADD COLUMN IF NOT EXISTS thread_id TEXT,
ADD COLUMN IF NOT EXISTS email_id UUID,
ADD COLUMN IF NOT EXISTS run_type TEXT NOT NULL DEFAULT 'email_ingest',
ADD COLUMN IF NOT EXISTS total_threads INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS completed_threads INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS waiting_threads INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS failed_threads INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS metadata JSON NOT NULL DEFAULT '{}'::json;

ALTER TABLE agent_runs
ALTER COLUMN started_at SET DEFAULT NOW(),
ALTER COLUMN updated_at SET DEFAULT NOW();

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'agent_runs'
          AND column_name = 'thread_id'
    ) THEN
        ALTER TABLE agent_runs ALTER COLUMN thread_id DROP NOT NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_agent_runs_batch_id
ON agent_runs(batch_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_thread_id
ON agent_runs(thread_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_email_id
ON agent_runs(email_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status
ON agent_runs(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS workflow_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id TEXT NOT NULL,
    agent_run_id INTEGER NOT NULL REFERENCES agent_runs(id),
    thread_id TEXT UNIQUE NOT NULL,
    email_id UUID NOT NULL REFERENCES email_events(id),
    source_message_id TEXT,
    sender TEXT,
    subject TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    current_node TEXT,
    stage TEXT NOT NULL DEFAULT 'INGESTING',
    cmir_status TEXT,
    latest_snapshot JSON,
    pending_action_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error TEXT
);

ALTER TABLE workflow_threads
ADD COLUMN IF NOT EXISTS batch_id TEXT;

UPDATE workflow_threads
SET batch_id = 'batch_legacy'
WHERE batch_id IS NULL;

ALTER TABLE workflow_threads
ALTER COLUMN batch_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workflow_threads_batch_id
ON workflow_threads(batch_id);

CREATE INDEX IF NOT EXISTS idx_workflow_threads_agent_run_id
ON workflow_threads(agent_run_id);

CREATE INDEX IF NOT EXISTS idx_workflow_threads_status
ON workflow_threads(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_workflow_threads_email_id
ON workflow_threads(email_id);

CREATE TABLE IF NOT EXISTS pending_human_actions (
    id SERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    agent_run_id INTEGER NOT NULL REFERENCES agent_runs(id),
    thread_id TEXT NOT NULL REFERENCES workflow_threads(thread_id),
    email_id UUID NOT NULL REFERENCES email_events(id),
    interrupt_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    state_snapshot JSON NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    answer JSONB,
    actor TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

ALTER TABLE pending_human_actions
ADD COLUMN IF NOT EXISTS batch_id TEXT;

UPDATE pending_human_actions
SET batch_id = 'batch_legacy'
WHERE batch_id IS NULL;

ALTER TABLE pending_human_actions
ALTER COLUMN batch_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_human_actions_one_open_per_thread
ON pending_human_actions(thread_id)
WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_pending_human_actions_batch_id
ON pending_human_actions(batch_id);

CREATE INDEX IF NOT EXISTS idx_pending_human_actions_agent_run_id
ON pending_human_actions(agent_run_id);

ALTER TABLE hitl_actions
ADD COLUMN IF NOT EXISTS batch_id TEXT,
ADD COLUMN IF NOT EXISTS thread_id TEXT,
ADD COLUMN IF NOT EXISTS action_type TEXT,
ADD COLUMN IF NOT EXISTS field_changes JSON;

CREATE INDEX IF NOT EXISTS idx_hitl_actions_batch_id
ON hitl_actions(batch_id);

CREATE INDEX IF NOT EXISTS idx_hitl_actions_thread_id
ON hitl_actions(thread_id);

ALTER TABLE agent_trace
ADD COLUMN IF NOT EXISTS batch_id TEXT,
ADD COLUMN IF NOT EXISTS thread_id TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_trace_batch_id
ON agent_trace(batch_id);

CREATE INDEX IF NOT EXISTS idx_agent_trace_thread_id
ON agent_trace(thread_id);
