# CMIR Resolution Agent (LangGraph, HITL, CLI)

A refactor of the original email → LLM extraction → DB script into a
SOLID, testable architecture, orchestrated by LangGraph with a real
human-in-the-loop (HITL) pause/resume flow. No frontend — approvals and
missing-field prompts happen on the console.

## Why this structure

Every folder maps to one SOLID concern:

| Folder | Responsibility | SOLID angle |
|---|---|---|
| `domain/` | `CMIR` model + `CMIRValidator` — pure business rules, no I/O | **S**ingle Responsibility. The validator, not the LLM, is the single source of truth for "is this complete?" |
| `interfaces/` | Abstract ports: `EmailReader`, `CMIRExtractor`, `*Repository`, `HumanReviewPort` | **D**ependency Inversion — the workflow depends on these, never on concrete IMAP/Postgres/Gemini classes |
| `infrastructure/` | Concrete adapters: Gmail IMAP, Gemini, Postgres, CLI prompts | **L**iskov substitution — any adapter can be swapped for another implementing the same port, e.g. `GeminiCMIRExtractor` → an OpenAI/Azure implementation, `CLIHumanReviewPort` → a future web "Workbench" |
| `workflow/` | `GraphState`, `WorkflowNodes`, `build_graph` — LangGraph wiring only | **O**pen/Closed — new steps are new nodes/edges, existing ones aren't touched |
| `container.py` | Composition root — the *only* file that imports concrete infrastructure classes | Keeps concrete choices in one place |
| `main.py` | Thin CLI loop: fetch emails → run graph → resolve interrupts | **I**nterface Segregation — depends only on the narrow ports it needs (`EmailReader.fetch_unread`, `HumanReviewPort`) |

## The graph

```
persist_email → extract_cmir → validate_cmir → persist_ai_result
    ├─ needs_input → collect_missing_fields ─┐
    │                                        └→ validate_cmir (loop)
    └─ ready → human_approval
                   ├─ approved → persist_cmir     → mark_email_read → END
                   └─ rejected → persist_rejection → mark_email_read → END
```

* `collect_missing_fields` and `human_approval` call LangGraph's
  `interrupt()`. The graph run genuinely pauses there; `main.py` catches
  the `__interrupt__` in the returned state, asks the console for an
  answer via `HumanReviewPort`, and resumes with `Command(resume=...)`.
* A `MemorySaver` checkpointer makes the pause/resume possible. For a
  long-running/production deployment, swap it for a Postgres-backed
  checkpointer (`langgraph-checkpoint-postgres`) so interrupts survive a
  process restart.
* If a mandatory field is still missing after the human answers,
  `route_after_validation` sends the run right back to
  `collect_missing_fields` instead of assuming success.

## Project layout

```
cmir_agent/
  domain/
    models.py          CMIR pydantic model, EmailMessage, mandatory fields
    validators.py       CMIRValidator (pure business rule)
  interfaces/
    email_reader.py      EmailReader port
    extractor.py          CMIRExtractor port
    repositories.py        EmailRepository / CMIRRepository / ActionLogRepository ports
    human_review.py         HumanReviewPort
  infrastructure/
    gmail_email_reader.py   IMAP implementation of EmailReader
    gemini_extractor.py      Gemini implementation of CMIRExtractor
    postgres_repositories.py  Postgres implementations of the repository ports
    cli_human_review.py        Console implementation of HumanReviewPort
    database.py                  psycopg2 connection factory
  workflow/
    state.py     GraphState TypedDict
    nodes.py       WorkflowNodes (node + routing functions, all DI'd)
    graph.py         build_graph(nodes, checkpointer)
  container.py   composition root (wires everything above)
  config.py       typed env-based config
  main.py          CLI entry point
migrations/schema.sql   email_events / email_action_log / cmir_records DDL
requirements.txt
.env.example
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in real values
psql -d cmir_db -f migrations/schema.sql
```

## Run

```bash
python -m cmir_agent.main
```

For each unread "CMIR" email found in the last day:

1. It's saved and sent to Gemini for extraction.
2. `CMIRValidator` checks the 7 mandatory fields.
   * Missing → you're prompted on the console for each missing field, the
     payload is re-validated, and (if still incomplete) you're prompted
     again.
3. Once complete, you're shown the full draft and asked `Approve and
   insert into DB? [y/n]`.
   * `y` → written to `cmir_records`, action logged, email marked read.
   * `n` → rejection logged (reason captured), **nothing is written to
     `cmir_records`**, email marked read.

## Testing

Because every I/O boundary is behind an interface, `WorkflowNodes` and
`CMIRValidator` can be unit-tested with in-memory fakes — no real IMAP
server, Postgres instance, or Gemini API key required. `domain/` has zero
framework dependencies, so it's the cheapest place to add coverage first.

## ⚠️ Security note

The original files you shared (`.env`, `config.py`) contained a live
Gmail app password and a Google API key in plain text. Please **rotate
both immediately** — revoke the Gmail app password in your Google Account
security settings and regenerate the Gemini API key — since they were
posted in this conversation. `.env.example` here only has placeholders;
real secrets should never be committed or pasted anywhere.
