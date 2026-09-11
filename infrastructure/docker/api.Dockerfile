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
# `infrastructure/local/docker-compose.yml` builds this image twice: once as the
# `api` service, and once as the one-shot `migrate` service that runs `alembic
# upgrade head` before the API is allowed to start.

# --------------------------------------------------------------------------
# uv — the resolver, taken from its own published image.
#
# A stage rather than `COPY --from=ghcr.io/astral-sh/uv:…` written inline,
# because Dependabot's docker ecosystem parses `FROM` and not `COPY --from`
# (dependabot-core#5103). Inline, the digest below would be pinned once and
# then never bumped again, which is the opposite of what pinning it is for.
# --------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv

# --------------------------------------------------------------------------
# Builder — resolve the workspace into a virtual environment.
#
# Both stages share one base image so the interpreter the virtual environment
# points at is byte-identical to the interpreter that runs it.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS builder

COPY --from=uv /uv /uvx /bin/

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

# The migration harness, copied *after* the sync so that editing a migration
# does not reinvalidate dependency resolution.
#
# `alembic` is already a runtime dependency of `tcg-api`, so the binary is in
# the virtual environment either way; what was missing was the configuration and
# the revisions themselves. Both paths in `alembic.ini` (`script_location`,
# `prepend_sys_path`) resolve against the working directory rather than against
# the file, and that directory is `/app` in both stages — so `alembic upgrade
# head` works in this image with no `-c` and no `cd`.
COPY alembic.ini ./
COPY database/ database/

# --------------------------------------------------------------------------
# Runtime — the environment and the source, run unprivileged.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

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
