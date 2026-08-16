# syntax=docker/dockerfile:1
#
# services/api — the FastAPI HTTP surface.
#
# Build from the REPOSITORY ROOT, not from this directory: `services/api` is a
# member of the uv workspace and cannot be resolved without the root
# `pyproject.toml`, `uv.lock` and its sibling members.
#
#   docker build -f infrastructure/docker/api.Dockerfile -t tcg-api:dev .
#
# Compose wiring arrives with #20.

# --------------------------------------------------------------------------
# Builder — resolve the workspace into a virtual environment.
#
# Both stages share one base image so the interpreter the virtual environment
# points at is byte-identical to the interpreter that runs it.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Workspace manifests first: dependency resolution is only reinvalidated when a
# manifest or the lockfile changes, not when source changes.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/ packages/
COPY services/ services/
COPY ml/ ml/

# `--frozen` fails rather than silently relocking, so the image is built from
# exactly the resolution that was reviewed and tested.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package tcg-api

# --------------------------------------------------------------------------
# Runtime — the environment and the source, run unprivileged.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Uploaded card images are untrusted input; nothing here needs root.
RUN groupadd --system --gid 1001 tcg \
    && useradd --system --uid 1001 --gid tcg --create-home tcg

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder --chown=tcg:tcg /app /app

USER tcg

EXPOSE 8000

# `/health` consults no dependency, so it is a true readiness signal for this
# container alone (spec §57 also has it report the application version).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"]

CMD ["uvicorn", "tcg_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
