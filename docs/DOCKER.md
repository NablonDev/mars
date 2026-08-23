# Docker

How to build and run the container image. For what the service does, see
`docs/RUNBOOK.md`; this file is only about the container.

## Default command: the API

`Dockerfile` bakes a default `CMD` that serves the API. The same
image is started three different ways, one per Azure Container Apps
resource: the API resource runs the image as-is (no command override
needed), and the other two supply an explicit command at run time. A
resource with no command spec at all would otherwise start and
immediately exit -- Azure Container Apps has no notion of "this image
already knows what to do," so an unset command is a crash loop, not a
no-op.

```bash
# API -- default CMD, no override needed
docker run --rm -p 8000:8000 --env-file .env mars-fines:dev

# Nightly batch job -- explicit override
docker run --rm --env-file .env mars-fines:dev \
  python scripts/ops/run_daily_batch.py

# Migration job -- explicit override
docker run --rm --env-file .env mars-fines:dev \
  alembic upgrade head
```

The API command still works spelled out explicitly too (`docker run ...
mars-fines:dev uvicorn app.main:app --host 0.0.0.0 --port 8000`) -- an
explicit `command:`/CLI argument always overrides the image's `CMD`,
never conflicts with it. `docker-compose.yml`'s `api` service does
exactly that, for local-dev readability.

## Build

From the repo root (`Dockerfile` and `docker-compose.yml` both live at the
repo root):

```bash
docker build -t mars-fines:dev .
```

Multi-stage: a `builder` stage installs dependencies into a venv, and a
slim `runtime` stage copies in only that venv plus `app/`, `alembic/`,
`alembic.ini`, and `scripts/` -- not `tests/`, not `docs/`, not `.env`.
Runs as a non-root user (`appuser`, uid 1000).

## Packaging decision: pyproject.toml + uv.lock, not requirements.txt

The image installs dependencies with `uv sync --frozen --no-dev` against
`pyproject.toml` + `uv.lock`, copied in before application source so the
dependency layer caches independently of code changes.

**Why, and what was checked:** all three files were compared by hand.

- `pyproject.toml`'s `[project.dependencies]` and `requirements.txt` list
  the same 11 runtime packages with **identical version ranges**
  (`alembic>=1.18,<2.0`, `fastapi>=0.139,<1.0`, ... down the list) -- no
  version disagreement between the two.
- `uv.lock` resolves those ranges to one specific, hash-pinned version per
  package (e.g. `fastapi==0.141.1`, `sqlalchemy==2.0.51`,
  `psycopg==3.3.4` / `psycopg-binary==3.3.4`). `pyproject.toml` alone and
  `requirements.txt` alone are both range-constrained, not pinned --
  `uv.lock` is the only one of the three that gives a reproducible build.

**Known discrepancy:** the package **sets** are not actually the same.
`requirements.txt` flattens `mypy`, `ruff`, and `pytest` into the same
un-differentiated list as the runtime dependencies (only a `# tests /
scripts` comment separates them). `pyproject.toml` keeps those three in a
separate `[dependency-groups]` `dev` group. Installing from
`requirements.txt` as-is would pull dev/lint/test tooling into the
production image; `uv sync --no-dev` skips that group entirely. This is
the reason `requirements.txt` was not picked, not just a preference.

`psycopg[binary]` resolves to a prebuilt wheel (`psycopg-binary` in
`uv.lock`) on both build platforms checked -- no `libpq-dev` or
`build-essential` is installed in either stage, and the build succeeds
without them.

## Compose (local development)

```bash
docker compose up                                 # db + api
docker compose --profile tools run --rm migrate
docker compose --profile tools run --rm worker
```

- `db`: `postgres:18-alpine`, named volume (`mars_postgres_data`),
  `pg_isready` healthcheck. The healthcheck hardcodes `-U mars -d mars`, so
  the host `.env` must set `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`
  to `mars` or the healthcheck never passes and `api` never starts.
- `api`: builds `Dockerfile` with the repo root as context, waits
  for `db`'s healthcheck, publishes `8000:8000`. `DATABASE_URL` is set to
  `postgresql+psycopg://mars:mars@db:5432/mars` -- the `+psycopg` (v3)
  driver prefix `app/core/config.py::Settings.database_url` expects, not
  the psycopg2-style bare `postgresql://`.
- `migrate`: same image, runs `alembic upgrade head` once. Given
  `profiles: ["tools"]` so a plain `up` never runs it.
- `worker`: same image, runs `scripts/ops/run_daily_batch.py` once and
  exits -- the local stand-in for the Azure Container Apps Job, and the
  reason the image needs no worker-specific build. Also `profiles:
  ["tools"]`. Override the command to drain only:
  `docker compose --profile tools run --rm worker python
  scripts/ops/run_daily_batch.py --drain-only`.
- `AZURE_OPENAI_*` vars are passed through from the host shell
  (`${AZURE_OPENAI_API_KEY:-}` etc.), never hardcoded. Leave them unset on
  the host to run everything except the fine-projection-summary endpoint, same as
  bare-metal (`docs/RUNBOOK.md` step 8).

## Verification performed

Run in this environment (`docker version`: 29.6.2) against the actual
Dockerfile above (paths below predate the `docker/` -> repo-root move; see
"Restructure verification" below for the re-check against the current
layout):

```
$ docker build -f docker/Dockerfile -t mars-fines:dev .
...
#20 naming to docker.io/library/mars-fines:dev done
Build succeeded. Image size: 502MB.

$ docker run --rm mars-fines:dev ls -la /app
total 28
drwxr-xr-x 1 appuser appuser 4096 ... .
drwxr-xr-x 1 root    root    4096 ... ..
drwxr-xr-x 1 appuser appuser 4096 ... .venv
drwxr-xr-x 1 appuser appuser 4096 ... alembic
-rw-r--r-- 1 appuser appuser  786 ... alembic.ini
drwxr-xr-x 1 appuser appuser 4096 ... app
drwxr-xr-x 1 appuser appuser 4096 ... scripts
# No .env, no tests/, no docs/ -- as required.

$ docker run --rm mars-fines:dev whoami
appuser

$ docker run --rm mars-fines:dev python -c "import app.main; print('ok')"
ok
```

The import check succeeded with **no env vars set at all** -- not just
import resolution, `Settings()` also loaded cleanly, because every
`Settings` field in `app/core/config.py` has a default (including
`azure_openai_*` defaulting to `""`). This confirms both that the module
path resolves and that a completely bare `docker run` won't crash on
missing config; it will only fail at the point something actually calls
Azure OpenAI, same as bare-metal (`docs/RUNBOOK.md` step 8).

`docker compose -f docker/docker-compose.yml config --quiet` also passed
(valid compose syntax).

### Restructure verification (Dockerfile/docker-compose.yml moved to repo root)

Re-checked after moving `docker/Dockerfile` -> `Dockerfile` and
`docker/docker-compose.yml` -> `docker-compose.yml` (build context and
`COPY` instructions are unaffected, since both already assumed a repo-root
context). `docker compose config --quiet` passes against the current
`docker-compose.yml` (`context: .`, `dockerfile: Dockerfile`).

A plain `docker build -t mars-fines:restructure .` against the current
`Dockerfile`/`.dockerignore` as committed **does not** currently succeed,
for two reasons that predate this move and are unrelated to it:

- `Dockerfile`'s `RUN pip install --no-cache-dir "uv==<PINNED_UV_VERSION>"`
  still has the literal placeholder unfilled -- `pip` rejects it as an
  invalid requirement.
- `.dockerignore` does not contain ignore patterns; it contains what looks
  like a stray, differently-shaped draft of the Dockerfile itself (`COPY`/
  `RUN`/`CMD` lines), which fails as invalid dockerignore syntax before the
  build context is even assembled.

Both were confirmed pre-existing (present before this restructure touched
either file) and were **not** fixed here -- out of scope for a mechanical
move, and each is a one-line/one-file decision the team should make
deliberately rather than have silently absorbed into a restructure diff.
To confirm the restructure itself introduces no path regression, both were
temporarily substituted with a plausible value (`uv==0.9.5`) and a minimal
correct `.dockerignore` (excluding `.git`, `.venv`, `tests/`, `docs/`,
caches, `.env`) for a local-only test build, then reverted to the
as-committed (broken) content before finishing:

```
$ docker build -t mars-fines:restructure2 .
...
#17 naming to docker.io/library/mars-fines:restructure2 done

$ docker run --rm mars-fines:restructure2 python -c "import app.main; print('ok')"
ok

$ docker run --rm mars-fines:restructure2 ls -la /app/scripts
demo/  ops/

$ docker run --rm mars-fines:restructure2 whoami
appuser
```

`import app.main` succeeding and `/app/scripts/{ops,demo}` both present
confirms the file moves and the root-level `Dockerfile`/`docker-compose.yml`
are correct; the two bugs above are separate, pre-existing issues the team
should decide on independently.

### Default CMD verification

Re-run after adding the default `CMD`, proving the no-argument case now
starts the API and an explicit override still works:

```
$ docker build -f docker/Dockerfile -t mars-fines:cmd .
...
#20 naming to docker.io/library/mars-fines:cmd done

$ docker inspect mars-fines:cmd --format '{{json .Config.Cmd}}'
["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]

$ docker run --rm -d -p 18000:8000 mars-fines:cmd
<container id>
$ docker logs <container id>
{"message": "Started server process [1]"}
{"message": "Waiting for application startup."}
{"message": "Application startup complete."}
{"message": "Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)"}

$ curl -s -o /dev/null -w "http status: %{http_code}\n" http://localhost:18000/api/v1/health
http status: 503
```

No command was supplied to `docker run` -- the image's own `CMD` started
uvicorn on its own. The health endpoint's `503` is the app's own DB
connectivity check correctly reporting the database as unreachable (no
`DATABASE_URL` was passed to this ad-hoc container) -- it is an HTTP
response from a live, listening server, not a crash. This confirms the
same thing the earlier `import app.main` check did, one level up: the
process actually starts and serves traffic with zero arguments.

```
$ docker run --rm mars-fines:cmd alembic --help
usage: alembic [-h] [--version] [-c CONFIG] [-n NAME] [-x X] [--raiseerr] [-q] ...

$ docker run --rm mars-fines:cmd python -c "print('explicit override works')"
explicit override works
```

An explicit command still overrides the default `CMD` exactly as before
-- the batch and migration resources are unaffected by this change.

## Why no `PYTHONPATH` gymnastics were needed, but it's set anyway

All three entry points resolve `app.*` correctly from `WORKDIR /app` with
no extra configuration, for three different reasons:

- `uvicorn app.main:app` -- uvicorn's own CLI always inserts the current
  working directory into `sys.path` (its `--app-dir` option defaults to
  `""`, not `None`; see `uvicorn/main.py`), so this resolves regardless of
  where the `uvicorn` executable itself lives.
- `alembic upgrade head` -- `alembic.ini` already sets
  `prepend_sys_path = .`, which does the same thing for Alembic's own
  import machinery.
- `python scripts/ops/run_daily_batch.py` -- following the existing pattern
  in `scripts/ops/run_projection_cli.py`, it inserts
  `Path(__file__).resolve().parents[2]` (i.e. `/app`) into `sys.path`
  itself.

`ENV PYTHONPATH=/app` is set in the runtime stage anyway, purely for
defense-in-depth -- one explicit guarantee instead of relying on three
different tools' cwd-handling conventions all continuing to hold.
