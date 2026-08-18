# syntax=docker/dockerfile:1

# Build context is the REPO ROOT:
#   docker build -t mars-fines:dev .

# ---------------------------------------------------------------------------
# Builder: resolve and install production dependencies into a venv.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency metadata separately so this expensive layer remains cached
# when application source code changes.
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------------------
# Runtime: slim, non-root image containing only the application and venv.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app"

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# Everything outside .dockerignore lands in the image, .env included if that
# file is ever wrong -- see its header and tests/unit/test_dockerignore.py.
COPY . .

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
