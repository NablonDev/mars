# Mars Petcare -- Projected Fines System

Forecasts, in advance of delivery, the retailer chargebacks Mars Petcare
is likely to incur on open orders -- driven by production shortfalls and
shipment delays within Mars's own control. See `docs/FINE_ENGINE.md` for
the full model methodology and open items, `docs/HOW_IT_WORKS.md` for a
completely traced, real-numbers example of how one database row becomes
one dollar figure, `docs/legacy/root-CLAUDE.md` for the detailed
architecture map (root `CLAUDE.md` was archived there and replaced by the
now-authoritative `./CLAUDE.local.md`), `docs/RUNBOOK.md` for every way to
set up and run this, and `./PROGRESS.local.md` for current status
(`docs/legacy/root-PROGRESS.md` has the full pre-restructure timeline).

For a presentation-ready walkthrough (scope rationale, the rules and
worked examples, the probability math, the current architecture), open
`docs/architecture-walkthrough.html` directly in a browser -- no server
needed, it's a single self-contained file.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/mars"

alembic upgrade head
```

## Running the API

```bash
uvicorn app.main:app --reload
```

Interactive docs at `http://127.0.0.1:8000/docs`. Then, in another terminal:

```bash
python scripts/seed_master_data.py           # master data + 4 example orders, via the API
python scripts/demo_daily_simulation.py    # replays all 4 scenarios day by day, via the API
```

Or run a projection without a server, straight against the database:

```bash
python scripts/run_projection_cli.py --order-id WMT-100234 --date 2026-08-05
python scripts/run_projection_cli.py --all-open
```

## Tests

```bash
pytest tests/ -v
```

77+ tests: the pure engine (boundary conditions, cap clamping, both
stacking modes, tiered pricing, the production-delay coupling), Alembic
migration/ORM parity, repository loading, and the full API surface
against an in-memory SQLite database -- no live Postgres required to run
the suite.

## Layout

Follows `docs/ARCHITECTURE.md`'s reference structure (adapted -- see
`docs/legacy/root-CLAUDE.md` for the one deliberate departure from it):

```
app/
  main.py                              -- FastAPI app factory
  core/config.py                       -- pydantic-settings
  api/
    dependencies.py                      -- FastAPI Depends() chain, no composition-root class
    v1/                                  -- routers (thin) + router.py aggregator
  schemas/                             -- Pydantic request/response models
  services/                            -- orchestration (projection, seeding, explanation)
  engine/                              -- pure, no I/O: models.py, shortage.py, delay.py,
                                           orchestrator.py, scenario_data.py (the 4 examples)
  agents/                              -- LLM layer: base.py, providers/, prompts/, tools/
  models/                              -- SQLAlchemy ORM, one module per table group
  db/
    base.py, session.py                  -- Declarative Base + surrogate-id helper, engine/session
  repositories/                        -- concrete repositories (no abstract ports layer)
alembic/, alembic.ini                  -- migrations (repo root)
tests/                                 -- pytest, in-memory SQLite
scripts/                               -- seed/demo/CLI scripts
data/samples/                          -- mock seed-data SQL snapshot
docs/                                  -- FINE_ENGINE.md, ARCHITECTURE.md, API.md, DATABASE.md,
                                           schema.sql, client-facing docx
```

Full architecture rationale, layer-by-layer, is in
`docs/legacy/root-CLAUDE.md` (see `./CLAUDE.local.md` for the current,
working instructions file).
